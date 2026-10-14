"""
distance - adaptador HTTP de la tool `distance`
Features dentro de un radio (ST_DWithin en EPSG:3857, réplica exacta).

Antes: `src/application/skills/spatial/distance_skill.py` del backend, con el SQL en la propia tool.
Ahora: valida argumentos y llama a `POST /api/v1/analisis/distancia` (docs/desacople/02-contrato.md).
"""

from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import DistanceParams

OPERATION = "distance"
PATH = "/api/v1/analisis/distancia"


async def execute(parameters: dict) -> Dict[str, Any]:
    try:
        params = DistanceParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))
    return await backend().call(OPERATION, PATH, params.model_dump())
