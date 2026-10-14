"""
statistics - adaptador HTTP de la tool `statistics`
Estadísticas descriptivas de una columna, con group_by opcional.

Antes: `src/application/skills/data/statistics_skill.py` del backend, con el SQL en la propia tool.
Ahora: valida argumentos y llama a `POST /api/v1/analisis/estadisticas` (docs/desacople/02-contrato.md).
"""

from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import StatisticsParams

OPERATION = "statistics"
PATH = "/api/v1/analisis/estadisticas"


async def execute(parameters: dict) -> Dict[str, Any]:
    try:
        params = StatisticsParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))
    result = await backend().call(OPERATION, PATH, params.model_dump())
    if not result.get("success", False):
        # Las skills incluían la capa en sus errores; el modelo la ve igual
        result.setdefault("layer", params.layer)
    return result
