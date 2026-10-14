"""
Orquestador Principal - v4.0 (FastAPI)
=======================================
Endpoint principal que recibe consultas y las enruta al LangGraph
multiagente con routing condicional, function calling y memoria conversacional.
"""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from agent_service.api.auth import AuthenticatedUser, get_current_user
from agent_service.api.schemas import OrchestratorRequest
from agent_service.memory.conversation_memory import conversation_memory
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
                "Por favor, proporciona un mensaje válido.", "empty_message"
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

            logger.info("Consulta procesada (LangGraph)")
            logger.info("=" * 60)
            return response
        except Exception as e:
            logger.error("Error en orquestador (LangGraph): %s", e, exc_info=True)
            return self._error_response(
                "Error interno del servidor.", "internal_error"
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
    son…") y el frontend la debe mostrar. Antes eso salía como 400, y el
    frontend, que corta en `!response.ok`, mostraba "Error HTTP: 400" en vez
    de la respuesta.
    """
    code = (response.get("error") or {}).get("code")
    if code == "empty_message":
        return 400
    if code == "internal_error":
        return 500
    if response.get("source") == "fallback" and response.get("intent") == "error":
        return 503  # proveedor de IA no disponible: dependencia externa
    return 200


@router.post("/orchestrator")
async def orchestrate(
    data: OrchestratorRequest, current_user: AuthenticatedUser = Depends(get_current_user)
):
    """
    POST /api/v1/orchestrator
    Body JSON: { "message": "...", "session_id": "...", "layersData": {...}, ... }
    """
    try:
        # Construir contexto desde el modelo Pydantic
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

        # El session_id lo elige el cliente y viajaba tal cual como clave del
        # historial conversacional: cualquier cuenta autenticada que adivinara
        # o reutilizara el session_id de otra podía leer su historial (posibles
        # direcciones, resultados de consultas espaciales) e inyectar mensajes
        # en esa sesión. Namespacing con el username del JWT hace que el mismo
        # session_id de dos usuarios distintos caiga en claves distintas, sin
        # tocar ConversationMemory (sigue viendo un string opaco).
        # El prefijo de longitud hace la clave inequívoca aunque el username
        # contenga ":" (cuentas creadas fuera de la API, que sí valida el formato).
        session_key = (
            f"{len(current_user.username)}:{current_user.username}:{data.session_id}"
            if data.session_id
            else ""
        )

        # Para correlacionar logs y trazas de la conversación. Se usa el
        # session_id del cliente, no session_key: esa lleva el username.
        # Vive en contextvars: lo ven el grafo, el LLM y los hilos de las skills.
        if data.session_id:
            session_id_var.set(data.session_id[:64])

        # Async de punta a punta: el event loop queda libre mientras se espera
        # al modelo y a las llamadas HTTP de las tools.
        response = await orchestrator_instance.process_query(
            data.message,
            context if context else None,
            session_key,
        )
        status_code = _http_status(response)

        return JSONResponse(content=response, status_code=status_code)

    except Exception as e:
        logger.error("Error en endpoint orchestrator: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "internal_server_error",
                    "message": "Error interno del servidor.",
                },
            },
        )
