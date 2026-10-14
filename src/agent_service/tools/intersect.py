"""
intersect - adaptador HTTP de la tool `intersect`
Features de table1 que intersectan con table2.

Antes: `src/application/skills/spatial/intersect_skill.py` del backend, con el SQL en la propia tool.
Ahora: valida argumentos y llama a `POST /api/v1/analisis/interseccion` (docs/desacople/02-contrato.md).
"""

from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import IntersectParams

OPERATION = "intersect"
PATH = "/api/v1/analisis/interseccion"


async def execute(parameters: dict) -> Dict[str, Any]:
    try:
        params = IntersectParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))
    return await backend().call(OPERATION, PATH, params.model_dump())
