"""
Schemas de la API del agente
=============================
Copiados sin cambios de `src/presentation/schemas.py` del backend: son el
contrato que ya usa el frontend en `POST /api/v1/orchestrator`.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    message: str = Field(..., min_length=1, description="Consulta del usuario")
    session_id: str = Field(default="", description="ID de sesión para memoria conversacional")

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


class OrchestratorResponse(BaseModel):
    success: bool
    reply: str
    agent_type: Optional[str] = None
    source: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    agent: Optional[str] = None
    bufferGeojson: Optional[Any] = None
    filteredGeojson: Optional[Any] = None
    newLayerId: Optional[str] = None
    layerName: Optional[str] = None
    result: Optional[str] = None
    error: Optional[Dict[str, Any]] = None


class AIRequest(BaseModel):
    message: str = Field(..., min_length=1)
    context: Dict[str, Any] = Field(default_factory=dict)
