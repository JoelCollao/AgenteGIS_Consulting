"""
count_features - adaptador HTTP de la tool `count_features`
Conteo de registros con filtro opcional.

Antes: `src/application/skills/data/count_features_skill.py` del backend, con el SQL en la propia tool.
Ahora: valida argumentos y llama a `POST /api/v1/analisis/conteo` (docs/desacople/02-contrato.md).
"""

from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import CountFeaturesParams

OPERATION = "count_features"
PATH = "/api/v1/analisis/conteo"


async def execute(parameters: dict) -> Dict[str, Any]:
    try:
        params = CountFeaturesParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))
    result = await backend().call(OPERATION, PATH, params.model_dump())
    if not result.get("success", False):
        # Las skills incluían la capa en sus errores; el modelo la ve igual
        result.setdefault("layer", params.layer)
    return result
