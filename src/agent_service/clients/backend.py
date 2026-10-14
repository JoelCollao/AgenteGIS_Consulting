"""
Cliente HTTP del backend GIS
=============================
Único camino por el que agent-service obtiene datos: la API del backend
(contrato en docs/desacople/02-contrato.md). Nada de drivers de base de datos.

Escrito a mano y no generado: los endpoints `/api/v1/analisis/*` aún no
existían en el OpenAPI del backend cuando se creó. Cuando estén publicados, se
puede regenerar con openapi-python-client manteniendo esta misma fachada.

Qué propaga en cada llamada (contrato §3.2):
  · Authorization: el JWT original del usuario. El backend aplica su
    `get_current_user` de siempre, que además comprueba que la cuenta sigue
    activa.
  · X-Request-ID / X-Session-ID: correlación de logs de punta a punta.

Qué devuelve: siempre un dict con la forma que las tools devolvían cuando
ejecutaban el SQL en proceso. En éxito, el body del backend tal cual; en
error, `{"type": "error", "operation", "error", "success": False}` con el
mensaje que el backend redactó para el modelo. `call` nunca lanza: el bucle
del agente necesita un resultado que devolverle al LLM.
"""

import asyncio
import contextvars
import logging
from typing import Any, Dict, Optional

import httpx

from agent_service.telemetry.observability import request_id_var, session_id_var

logger = logging.getLogger(__name__)

# JWT del usuario de la petición en curso. Lo fija el endpoint de chat; las
# tools corren en tareas de asyncio, que heredan el contexto.
auth_token_var: contextvars.ContextVar[str] = contextvars.ContextVar("auth_token", default="")

_USER_AGENT = "agent-service/1.0"
# Reintento único: las operaciones del contrato actual son lecturas.
_RETRY_STATUS = {503}


class BackendClient:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_connections: int = 20,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            # Tope GLOBAL del proceso: el semáforo del agente es por ronda, así
            # que N peticiones simultáneas podrían abrir 4×N conexiones.
            limits=httpx.Limits(max_connections=max_connections),
            headers={"User-Agent": _USER_AGENT},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        token = auth_token_var.get()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request_id = request_id_var.get()
        if request_id and request_id != "-":
            headers["X-Request-ID"] = request_id
        session_id = session_id_var.get()
        if session_id and session_id != "-":
            headers["X-Session-ID"] = session_id
        return headers

    async def call(self, operation: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST `payload` a `path` y normaliza la respuesta (ver docstring del módulo)."""
        response: Optional[httpx.Response] = None
        for attempt in (1, 2):
            try:
                response = await self._client.post(path, json=payload, headers=self._headers())
            except httpx.TransportError as exc:
                if attempt == 1:
                    logger.warning("[backend] %s %s falló (%s); reintentando", operation, path, exc)
                    await asyncio.sleep(0.2)
                    continue
                return _error(operation, f"Backend no disponible: {exc.__class__.__name__}")
            if response.status_code in _RETRY_STATUS and attempt == 1:
                await asyncio.sleep(0.2)
                continue
            break

        assert response is not None
        if response.is_success:
            try:
                body = response.json()
            except ValueError:
                return _error(operation, "Respuesta del backend no es JSON")
            if isinstance(body, dict):
                return body
            return _error(operation, "Respuesta del backend con formato inesperado")

        return _error(operation, _error_message(response))


def _error(operation: str, message: str) -> Dict[str, Any]:
    return {"type": "error", "operation": operation, "error": message, "success": False}


def _error_message(response: httpx.Response) -> str:
    """
    Extrae el mensaje para el modelo de los tres formatos de error posibles:
      · contrato §1.1:          {"error": {"code", "message"}}
      · 422 de FastAPI:         {"detail": [{"loc", "msg", ...}, ...]}
      · HTTPException / 401:    {"detail": "texto"}
    """
    try:
        body = response.json()
    except ValueError:
        return f"Error del backend (HTTP {response.status_code})"

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        detail = body.get("detail")
        if isinstance(detail, str):
            if response.status_code == 401:
                return f"No autorizado: {detail}"
            return detail
        if isinstance(detail, list) and detail:
            first = detail[0] if isinstance(detail[0], dict) else {}
            loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
            msg = str(first.get("msg", "argumentos no válidos"))
            return f"{loc}: {msg}" if loc else msg
    return f"Error del backend (HTTP {response.status_code})"
