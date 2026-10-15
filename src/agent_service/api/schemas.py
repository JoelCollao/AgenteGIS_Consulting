"""
Schemas de la API del agente
=============================
El request se copió de `src/presentation/schemas.py` del backend: es el
contrato que ya usa el frontend en `POST /api/v1/orchestrator`. Solo
`message` es obligatorio; el resto es contexto opcional del visor.

Los errores de TODA la API comparten el sobre `ErrorResponse` (ver
`api/errors.py`), así el cliente los trata igual venga de donde venga el fallo.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class LayerInfo(BaseModel):
    """Información de una capa del mapa enviada por el frontend."""
    id: Optional[str] = None
    name: str
    geometryType: Optional[str] = None
    featureCount: Optional[int] = None
    fields: Optional[List[str]] = None


class MapContext(BaseModel):
    """Contexto del estado del mapa en el visor del frontend."""
    layers: List[LayerInfo] = Field(default_factory=list)
    totalVisibleLayers: Optional[int] = None
    totalFeatures: Optional[int] = None
    byGeometryType: Optional[Dict[str, int]] = None


class ActionRequest(BaseModel):
    """Cuando el frontend ya sabe qué operación quiere (botón directo, no chat)."""
    skill: str = Field(..., description="Nombre de la skill a ejecutar")
    params: Dict[str, Any] = Field(default_factory=dict)


class OrchestratorRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="Consulta del usuario")
    session_id: str = Field(
        default="", max_length=128, description="ID de sesión para memoria conversacional"
    )

    # Contexto del mapa (estructura tipada + retrocompatibilidad)
    layersData: Optional[Dict[str, Any]] = None
    layersGeojson: Optional[List[Dict[str, Any]]] = None
    features: Optional[List[Dict[str, Any]]] = None
    targetLayers: Optional[List[Dict[str, Any]]] = None

    # Parámetros pre-parseados del frontend
    bufferRequest: Optional[Dict[str, Any]] = None
    filterRequest: Optional[Dict[str, Any]] = None
    distance: Optional[float] = None
    dissolve: Optional[bool] = None
    filterField: Optional[str] = None
    filterValue: Optional[str] = None

    # Acción directa (cuando el frontend sabe qué skill ejecutar)
    action: Optional[ActionRequest] = None

    # Contexto libre (retrocompatibilidad)
    context: Optional[Dict[str, Any]] = None

    @field_validator("message")
    @classmethod
    def _message_no_vacio(cls, v: str) -> str:
        # min_length no basta: "   " pasaba y se rechazaba más tarde con otro código.
        if not v.strip():
            raise ValueError("el mensaje no puede estar vacío")
        return v.strip()


# Ejemplos del cuerpo para Swagger (desplegable "Examples"). Sin ellos,
# Swagger rellena TODOS los campos opcionales con valores de relleno
# ("additionalProp1"...) y parece que fueran obligatorios.
ORCHESTRATOR_REQUEST_EXAMPLES: Dict[str, Dict[str, Any]] = {
    "minimo": {
        "summary": "Pregunta simple (solo message)",
        "value": {"message": "¿Qué es un buffer?"},
    },
    "con_sesion": {
        "summary": "Con memoria conversacional",
        "value": {"message": "¿Y para qué sirve?", "session_id": "demo-1"},
    },
    "con_capas": {
        "summary": "Con las capas del mapa (como lo envía el visor)",
        "value": {
            "message": "¿Cuántos registros tiene la capa predios?",
            "session_id": "demo-1",
            "layersData": {
                "totalVisibleLayers": 1,
                "totalFeatures": 120,
                "layers": [
                    {
                        "name": "predios",
                        "geometryType": "Polygon",
                        "featureCount": 120,
                        "fields": ["id", "area"],
                    }
                ],
            },
        },
    },
    "buffer": {
        "summary": "Buffer desde el formulario del visor",
        "value": {
            "message": "Haz un buffer de 50 metros a la capa colegios",
            "targetLayers": [{"name": "colegios", "source": "postgis"}],
            "bufferRequest": {"distance": 50, "unit": "meters"},
        },
    },
}


class ToolExecuted(BaseModel):
    name: str
    success: bool


class OrchestratorResponse(BaseModel):
    """Respuesta 200. `success=false` aquí significa que la skill falló pero
    el modelo redactó una explicación útil en `reply` (p. ej. "la columna X no
    existe, las disponibles son…"): no es un error HTTP."""

    success: bool = Field(..., description="Si la última skill ejecutada funcionó")
    reply: str = Field(..., description="Texto para el usuario (markdown)")
    agent_type: Optional[str] = Field(None, description="'conversational', 'map_context' o la skill usada")
    source: Optional[str] = Field(None, description="'conversational' | 'map_context' | 'skill'")
    metadata: Optional[Dict[str, Any]] = Field(None, description="intent, confianza, processing_time_ms…")
    agent: Optional[str] = Field(None, description="'AgenteBuffer' | 'AgenteFiltro' si se generó capa")
    bufferGeojson: Optional[Any] = None
    filteredGeojson: Optional[Any] = None
    newLayerId: Optional[str] = None
    layerName: Optional[str] = None
    result: Optional[str] = Field(None, description="Alias de reply cuando se genera capa")
    tools_executed: Optional[List[ToolExecuted]] = None


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Código estable para el cliente, p. ej. 'unauthorized'")
    message: str = Field(..., description="Mensaje legible")
    details: Optional[Any] = Field(None, description="Campos inválidos (422) u otra información")


class ErrorResponse(BaseModel):
    """Sobre común de todos los errores (4xx/5xx)."""

    success: bool = False
    error: ErrorDetail
    reply: Optional[str] = Field(
        None, description="Mensaje mostrable en el chat (retrocompatibilidad con el frontend)"
    )
    request_id: Optional[str] = Field(None, description="Para buscar la petición en los logs")

