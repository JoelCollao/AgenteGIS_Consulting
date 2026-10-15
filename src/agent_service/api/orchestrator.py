"""
Orquestador Principal - v4.0 (FastAPI)
=======================================
Endpoint principal que recibe consultas y las enruta al LangGraph
multiagente con routing condicional, function calling y memoria conversacional.
"""

import asyncio
import json
import logging
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from agent_service.api import errors
from agent_service.api.auth import AuthenticatedUser, get_current_user
from agent_service.api.payload_log import log_payload
from agent_service.api.schemas import (
    ORCHESTRATOR_REQUEST_EXAMPLES,
    OrchestratorRequest,
    OrchestratorResponse,
)
from agent_service.config import settings
from agent_service.memory.conversation_memory import conversation_memory
from agent_service.orchestrator.streaming import stream_sink_var
from agent_service.telemetry.observability import session_id_var

logger = logging.getLogger(__name__)

router = APIRouter()


class Orchestrator:
    """Orquestador con memoria conversacional — delega todo a LangGraph"""

    async def process_query(
        self, message: str, context: Optional[Dict] = None, session_id: str = ""
    ) -> Dict:
        """Procesar una consulta del usuario usando LangGraphOrchestrator"""
        from agent_service.orchestrator.langgraph_flow import (
            orchestrator as langgraph_orchestrator,
        )

        logger.info("=" * 60)
        logger.info("ORQUESTADOR v4.0 - Nueva consulta (LangGraph)")
        logger.info("Mensaje: %s...", message[:100])
        if session_id:
            logger.info("Session: %s", session_id)
        if context:
            logger.info("Contexto: %s", list(context.keys()))

        if not message or not message.strip():
            logger.warning("Mensaje vacío recibido")
            return self._error_response(
                "Por favor, proporciona un mensaje válido.", errors.VALIDATION_ERROR
            )

        try:
            # Recuperar historial conversacional
            conversation_history = conversation_memory.get_history(session_id)

            # Guardar mensaje del usuario en memoria
            if session_id:
                conversation_memory.add_turn(session_id, "user", message)

            input_data = {
                "message": message,
                "context": context if context else {},
                "conversation_history": conversation_history,
                "session_id": session_id,
            }
            result = await langgraph_orchestrator.arun(input_data)

            from dataclasses import asdict, is_dataclass

            if is_dataclass(result):
                response = asdict(result)
            else:
                response = result

            # Guardar respuesta del asistente en memoria
            reply = response.get("reply", "")
            if session_id and reply:
                conversation_memory.add_turn(session_id, "assistant", reply)

            from agent_service.models import default_llm_provider

            llm = default_llm_provider()
            logger.info(
                "Consulta procesada (LangGraph) | LLM: %s / %s",
                llm.get_provider_type(), llm.get_model(),
            )
            # También en la respuesta, para verlo en el log de payload y en Postman
            metadata = response.get("metadata")
            if isinstance(metadata, dict):
                metadata["llm_provider"] = llm.get_provider_type()
                metadata["llm_model"] = llm.get_model()
            logger.info("=" * 60)
            return response
        except Exception as e:
            logger.error("Error en orquestador (LangGraph): %s", e, exc_info=True)
            return self._error_response(
                "Error interno del servidor.", errors.INTERNAL_ERROR
            )

    def _error_response(
        self, message: str, error_code: str, details: Optional[str] = None
    ) -> Dict:
        response = {
            "success": False,
            "reply": message,
            "error": {"code": error_code, "message": message},
        }
        if details:
            response["error"]["details"] = details
        return response


# Instancia global (singleton)
orchestrator_instance = Orchestrator()


def _http_status(response: Dict) -> int:
    """
    Código HTTP según POR QUÉ falló, no según `success`.

    `success` dice si la última skill funcionó; aunque falle, el modelo
    redacta una respuesta útil ("no encontré la columna X, las disponibles
    son…") y el frontend la debe mostrar: eso es un 200. Solo un `error`
    con código (lo ponen el orquestador y el fallback del agente) es un
    error HTTP; la tabla código → HTTP está en api/errors.py.
    """
    code = (response.get("error") or {}).get("code")
    if not code:
        return 200
    return errors.STATUS_BY_CODE.get(code, 500)


def _to_http(response: Dict) -> Tuple[int, Dict]:
    """(código HTTP, cuerpo). Los errores salen con el sobre común ErrorResponse."""
    status_code = _http_status(response)
    if status_code < 400:
        return status_code, response
    error = response.get("error") or {}
    details = error.get("details") if settings.debug_errors else None
    return status_code, errors.error_body(
        error.get("code", errors.INTERNAL_ERROR),
        error.get("message") or response.get("reply") or "Error interno del servidor.",
        details,
    )


def _build_context(data: OrchestratorRequest) -> Dict:
    """Contexto del agente a partir del cuerpo de la petición."""
    log_payload("request", data.model_dump(exclude_none=True))
    context = {}
    for field in (
        "layersData", "layersGeojson", "features", "targetLayers",
        "bufferRequest", "filterRequest", "distance", "dissolve",
        "filterField", "filterValue",
    ):
        value = getattr(data, field, None)
        if value is not None:
            context[field] = value

    if data.context:
        context.update(data.context)

    # Acción directa del frontend (bypass del LLM)
    if data.action:
        context["direct_action"] = {
            "skill": data.action.skill,
            "params": data.action.params,
        }

    logger.info(
        "Request: message='%s...', session_id='%s', context_keys=%s",
        data.message[:50], data.session_id, list(context.keys()),
    )
    return context


def _session_key(data: OrchestratorRequest, current_user: AuthenticatedUser) -> str:
    """
    Clave del historial conversacional.

    El session_id lo elige el cliente y viajaba tal cual como clave del
    historial conversacional: cualquier cuenta autenticada que adivinara
    o reutilizara el session_id de otra podía leer su historial (posibles
    direcciones, resultados de consultas espaciales) e inyectar mensajes
    en esa sesión. Namespacing con el username del JWT hace que el mismo
    session_id de dos usuarios distintos caiga en claves distintas, sin
    tocar ConversationMemory (sigue viendo un string opaco).
    El prefijo de longitud hace la clave inequívoca aunque el username
    contenga ":" (cuentas creadas fuera de la API, que sí valida el formato).
    """
    # Para correlacionar logs y trazas de la conversación. Se usa el
    # session_id del cliente, no la clave: esa lleva el username.
    # Vive en contextvars: lo ven el grafo, el LLM y los hilos de las skills.
    if data.session_id:
        session_id_var.set(data.session_id[:64])
    return (
        f"{len(current_user.username)}:{current_user.username}:{data.session_id}"
        if data.session_id
        else ""
    )


_REQUEST_BODY = Body(openapi_examples=ORCHESTRATOR_REQUEST_EXAMPLES)


@router.post(
    "/orchestrator",
    summary="Chat con el asistente GIS",
    response_model=OrchestratorResponse,
    responses=errors.ERROR_RESPONSES,
)
async def orchestrate(
    data: OrchestratorRequest = _REQUEST_BODY,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Envía una pregunta al asistente y devuelve la respuesta completa.

    - **Autenticación:** `Authorization: Bearer <JWT>` del login del backend
      (en Swagger, botón **Authorize**).
    - **Cuerpo:** solo `message` es obligatorio. El resto es el contexto que
      envía el visor (capas, buffer, filtro); ver los ejemplos.
    - **200** también cuando una operación GIS falla: `success=false` y la
      explicación en `reply`. Los errores HTTP usan el formato `ErrorResponse`.
    """
    try:
        context = _build_context(data)
        session_key = _session_key(data, current_user)

        # Async de punta a punta: el event loop queda libre mientras se espera
        # al modelo y a las llamadas HTTP de las tools.
        response = await orchestrator_instance.process_query(
            data.message,
            context if context else None,
            session_key,
        )
        status_code, body = _to_http(response)
        log_payload(f"response (HTTP {status_code})", body)
        return JSONResponse(content=body, status_code=status_code)

    except Exception as e:
        logger.error("Error en endpoint orchestrator: %s", e, exc_info=True)
        return errors.error_response(errors.INTERNAL_ERROR, "Error interno del servidor.")


def _sse(event: Dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


_STREAM_EXAMPLE = (
    'data: {"type": "status", "text": "Ejecutando count_features…"}\n\n'
    'data: {"type": "delta", "text": "La capa predios "}\n\n'
    'data: {"type": "delta", "text": "tiene 120 registros."}\n\n'
    'data: {"type": "done", "status": 200, "response": {"success": true, '
    '"reply": "La capa predios tiene 120 registros.", "...": "..."}}\n\n'
)


@router.post(
    "/orchestrator/stream",
    summary="Chat con el asistente GIS (streaming SSE)",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events: `status`, `delta` y un `done` final.",
            "content": {"text/event-stream": {"example": _STREAM_EXAMPLE}},
        },
        401: errors.ERROR_RESPONSES[401],
        422: errors.ERROR_RESPONSES[422],
    },
)
async def orchestrate_stream(
    data: OrchestratorRequest = _REQUEST_BODY,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    POST /api/v1/orchestrator/stream — mismo cuerpo que /orchestrator.

    Responde con Server-Sent Events (`data: {...}\n\n`):
      - `status`: el agente está ejecutando tools ("Ejecutando count_features…")
      - `delta`:  fragmento del texto de la respuesta, según lo genera el modelo
      - `done`:   la respuesta completa, idéntica a la de /orchestrator, y el
                  código HTTP que habría tenido (`status`). El `reply` de aquí
                  es el definitivo: sustituye al texto acumulado con los deltas.
    El código HTTP del stream es siempre 200; los errores llegan en `done`.
    """
    context = _build_context(data)
    session_key = _session_key(data, current_user)
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        stream_sink_var.set(queue.put_nowait)
        try:
            response = await orchestrator_instance.process_query(
                data.message, context if context else None, session_key
            )
            status_code, body = _to_http(response)
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Error en endpoint orchestrator/stream: %s", e, exc_info=True)
            status_code = 500
            body = errors.error_body(errors.INTERNAL_ERROR, "Error interno del servidor.")
        done = {"type": "done", "status": status_code, "response": body}
        log_payload(f"response stream done (status {done['status']})", done["response"])
        queue.put_nowait(done)

    # La tarea se crea aquí y no dentro del generador: así copia el contexto
    # de la petición (JWT para el backend, request_id, session_id) tal cual
    # lo dejaron la dependencia de auth y el middleware.
    task = asyncio.create_task(produce())

    async def events():
        try:
            while True:
                event = await queue.get()
                yield _sse(event)
                if event["type"] == "done":
                    break
        finally:
            # Cliente desconectado a mitad: no seguir gastando modelo ni backend.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
