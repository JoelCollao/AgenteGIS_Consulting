"""
Skill Registry - Registro Central de Habilidades GIS
=====================================================
Registro dinámico que reúne todas las skills disponibles.
Las skills se registran automáticamente al importar el módulo.

Para agregar una nueva skill:
  1. Crear el archivo en tools/ (un adaptador HTTP por tool).
  2. Implementar `async def execute(parameters: dict) -> dict`.
  3. Importarla aquí, añadirla a skills_registry y darle política en
     config/policies.yaml (sin política, queda bloqueada).

Cambio respecto al backend: `execute_skill` es async. Las tools HTTP son
corrutinas; las síncronas (las de mapa, o dobles de test) se llevan a un hilo
para que las de una misma ronda sigan corriendo en paralelo.
"""

import asyncio
import inspect
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from agent_service.telemetry.observability import span  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Importación de todas las skills disponibles
# ---------------------------------------------------------------------------
from agent_service.policies.engine import APPROVAL, AUTO, policy_engine  # noqa: E402
from agent_service.tools import (  # noqa: E402
    buffer as buffer_skill,
    count_features as count_features_skill,
    distance as distance_skill,
    filter_features as filter_features_skill,
    highlight_features as highlight_features_skill,
    intersect as intersect_skill,
    statistics as statistics_skill,
    zoom_layer as zoom_layer_skill,
)

# ---------------------------------------------------------------------------
# Registro central: nombre_skill -> función execute
# ---------------------------------------------------------------------------
skills_registry: Dict[str, Callable[[dict], Any]] = {
    # Spatial
    "buffer_analysis":   buffer_skill.execute,
    "intersect":         intersect_skill.execute,
    "distance":          distance_skill.execute,
    # Data
    "count_features":    count_features_skill.execute,
    "filter_features":   filter_features_skill.execute,
    "statistics":        statistics_skill.execute,
    # Map
    "highlight_features": highlight_features_skill.execute,
    "zoom_layer":         zoom_layer_skill.execute,
}


def get_skill(skill_name: str) -> Optional[Callable[[dict], Any]]:
    """
    Retorna la función execute de una skill por su nombre.

    Args:
        skill_name: Nombre registrado de la skill.

    Returns:
        Función execute o None si no existe.
    """
    skill = skills_registry.get(skill_name)
    if skill is None:
        logger.warning("[SkillRegistry] Skill '%s' no encontrada en el registro.", skill_name)
    return skill


def list_skills() -> List[str]:
    """Retorna la lista de nombres de skills disponibles."""
    return list(skills_registry.keys())


async def execute_skill(skill_name: str, parameters: dict) -> dict:
    """
    Ejecuta una skill por nombre con los parámetros dados.

    Args:
        skill_name:  Nombre de la skill a ejecutar.
        parameters:  Diccionario de parámetros estructurados.

    Returns:
        Resultado JSON estructurado de la skill.
    """
    skill = get_skill(skill_name)
    if skill is None:
        return {
            "type": "error",
            "operation": skill_name,
            "error": f"Skill '{skill_name}' no disponible. Skills válidas: {list_skills()}",
            "success": False,
        }

    mode = policy_engine().decide(skill_name)
    if mode != AUTO:
        reason = (
            "requiere aprobación humana y no se ejecutó"
            if mode == APPROVAL
            else "está bloqueada por política"
        )
        logger.warning("[SkillRegistry] Skill '%s' no ejecutada: %s", skill_name, mode,
                       extra={"event": "tool.policy", "tool_name": skill_name, "policy": mode})
        return {
            "type": "error",
            "operation": skill_name,
            "error": f"La operación '{skill_name}' {reason}.",
            "success": False,
        }

    with span(
        f"execute_tool {skill_name}",
        **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": skill_name},
    ) as current:
        start = time.perf_counter()
        try:
            logger.info("[SkillRegistry] Ejecutando skill='%s' params=%s", skill_name, _params_for_log(parameters))
            if inspect.iscoroutinefunction(skill):
                result = await skill(parameters)
            else:
                result = await asyncio.to_thread(skill, parameters)
            _compact_error(result)
            if result.get("success", False):
                logger.info("[SkillRegistry] Skill '%s' completada exitosamente.", skill_name)
            else:
                logger.warning(
                    "[SkillRegistry] Skill '%s' retornó fallo: %s",
                    skill_name, result.get("error", "sin detalle")
                )
        except Exception as exc:
            logger.error("[SkillRegistry] Skill '%s' falló con excepción: %s", skill_name, exc, exc_info=True)
            result = {
                "type": "error",
                "operation": skill_name,
                "error": str(exc),
                "success": False,
            }

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        success = bool(result.get("success", False))
        current.set_attribute("tool.success", success)
        logger.info(
            "[SkillRegistry] tool=%s success=%s %.1f ms",
            skill_name, success, duration_ms,
            extra={
                "event": "tool.call",
                "tool_name": skill_name,
                "tool_success": success,
                "duration_ms": duration_ms,
            },
        )
        return result


def _params_for_log(parameters: dict) -> dict:
    """Parámetros sin las features GeoJSON (pueden ser miles de vértices)."""
    if not isinstance(parameters, dict):
        return parameters
    return {
        k: (f"<{len(v)} features>" if k == "features" and isinstance(v, list) else v)
        for k, v in parameters.items()
    }


def _compact_error(result: dict) -> None:
    """
    Deja solo la primera línea del error de una skill.

    Las excepciones de SQLAlchemy traen la sentencia SQL completa y un enlace
    de ayuda; eso ya queda en el log de la skill con su traceback. Repetirlo
    ensucia el log y, peor, viaja al modelo como resultado de la tool, que
    podría citar el SQL (y la estructura de la base) en su respuesta.
    """
    error = result.get("error") if isinstance(result, dict) else None
    if isinstance(error, str) and "\n" in error:
        result["error"] = error.strip().splitlines()[0]
