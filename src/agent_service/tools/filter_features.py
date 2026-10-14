"""
filter_features - adaptador HTTP de la tool `filter_features`
Filtro por atributo o búsqueda libre (column='__search__').

Antes: `src/application/skills/data/filter_features_skill.py` del backend, con el SQL en la propia tool.
Ahora: valida argumentos y llama a `POST /api/v1/analisis/filtro` (docs/desacople/02-contrato.md).
"""

from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import FilterFeaturesParams

OPERATION = "filter_features"
PATH = "/api/v1/analisis/filtro"


async def execute(parameters: dict) -> Dict[str, Any]:
    try:
        params = FilterFeaturesParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))
    result = await backend().call(OPERATION, PATH, params.model_dump())
    if not result.get("success", False):
        # Las skills incluían la capa en sus errores; el modelo la ve igual
        result.setdefault("layer", params.layer)
    return result
