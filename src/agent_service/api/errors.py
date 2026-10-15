"""
Errores de la API: un solo formato y un código HTTP por causa
==============================================================
Antes cada fallo tenía su forma: `{"detail": "..."}` en el 401,
`{"detail": [...]}` en el 422, `{"error": "..."}` en el 500 y
`{"success": false, "reply": ...}` en el 503. Ahora todos devuelven
`ErrorResponse`:

    {"success": false,
     "error": {"code": "unauthorized", "message": "...", "details": ...},
     "reply": "...",          ← mismo mensaje, para el chat del frontend
     "request_id": "..."}

Código HTTP según la causa:
    200  la consulta se procesó (aunque la skill fallara: lo explica `reply`)
    401  falta el token o no es válido
    422  el cuerpo no es válido (campo obligatorio ausente, tipo incorrecto…)
    500  fallo interno del servicio
    503  el proveedor de IA no está disponible
    504  el proveedor de IA no respondió a tiempo
"""

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_service.api.schemas import ErrorResponse
from agent_service.config import settings
from agent_service.telemetry.observability import request_id_var

logger = logging.getLogger(__name__)

# Códigos estables que el cliente puede usar en un `switch`
UNAUTHORIZED = "unauthorized"
VALIDATION_ERROR = "validation_error"
INTERNAL_ERROR = "internal_error"
LLM_UNAVAILABLE = "llm_unavailable"
LLM_TIMEOUT = "llm_timeout"

STATUS_BY_CODE = {
    UNAUTHORIZED: 401,
    VALIDATION_ERROR: 422,
    INTERNAL_ERROR: 500,
    LLM_UNAVAILABLE: 503,
    LLM_TIMEOUT: 504,
}

_CODE_BY_STATUS = {
    401: UNAUTHORIZED,
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: VALIDATION_ERROR,
}


def error_body(code: str, message: str, details: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "success": False,
        "error": error,
        "reply": message,
        "request_id": request_id_var.get() or None,
    }


def error_response(
    code: str,
    message: str,
    details: Any = None,
    status_code: Optional[int] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code or STATUS_BY_CODE.get(code, 500),
        content=error_body(code, message, details),
        headers=headers,
    )


def _validation_details(exc: RequestValidationError):
    """[{field: "message", message: "..."}] sin el prefijo 'body' de FastAPI."""
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        details.append({"field": ".".join(loc) or "body", "message": err.get("msg", "")})
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_request: Request, exc: StarletteHTTPException):
        code = _CODE_BY_STATUS.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(
            code, str(exc.detail), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception(_request: Request, exc: RequestValidationError):
        return error_response(
            VALIDATION_ERROR, "El cuerpo de la petición no es válido.", _validation_details(exc)
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception):
        logger.error(
            "Excepción no controlada en %s %s: %s", request.method, request.url.path, exc,
            exc_info=exc, extra={"event": "http.unhandled_exception"},
        )
        return error_response(
            INTERNAL_ERROR,
            "Error interno del servidor.",
            details=str(exc) if settings.debug_errors else None,
        )


def _doc(description: str, code: str, message: str, details: Any = None) -> Dict[str, Any]:
    example = error_body(code, message, details)
    example["request_id"] = "edfb53c32c11479995b365c40d2f2486"
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": example}},
    }


# Para `responses=` de las rutas: lo que muestra Swagger por cada código.
ERROR_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    401: _doc(
        "Falta el token o no es válido. En Swagger: botón **Authorize**.",
        UNAUTHORIZED, "No autenticado: falta el header Authorization",
    ),
    422: _doc(
        "Cuerpo inválido: falta `message`, está vacío o un campo tiene un tipo incorrecto.",
        VALIDATION_ERROR, "El cuerpo de la petición no es válido.",
        [{"field": "message", "message": "Field required"}],
    ),
    500: _doc("Fallo interno del servicio.", INTERNAL_ERROR, "Error interno del servidor."),
    503: _doc(
        "El proveedor de IA no está disponible (credenciales, cuota, modelo inexistente…).",
        LLM_UNAVAILABLE, "El asistente de IA no está disponible en este momento.",
    ),
    504: _doc(
        "El proveedor de IA no respondió a tiempo.",
        LLM_TIMEOUT, "El asistente de IA tardó demasiado en responder.",
    ),
}
