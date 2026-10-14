"""
buffer - tool `buffer_analysis`
================================
Dos modos, igual que la skill original (`src/application/skills/spatial/buffer_skill.py`):

**Modo PostGIS** (`table`): ahora es una llamada a `POST /api/v1/analisis/buffer`.
El backend ejecuta ST_Buffer sobre geography y devuelve el mismo resultado.

**Modo in-memory** (`features`): se queda aquí (decisión P2 de la Fase 1).
Buffer con Shapely + PyProj sobre las features GeoJSON del frontend (archivos
importados), con reproyección UTM automática. No toca base de datos ni backend.
El código es el original, sin cambios.
"""

import asyncio
import logging
from typing import Any, Dict

from agent_service.tools.base import backend, tool_error
from agent_service.tools.params import BufferParams

logger = logging.getLogger(__name__)

OPERATION = "buffer_analysis"
PATH = "/api/v1/analisis/buffer"

_UNIT_TO_METERS = {
    "meters": 1.0,
    "km":     1000.0,
    "miles":  1609.344,
}


async def execute(parameters: dict) -> Dict[str, Any]:
    """Punto de entrada: valida parámetros y bifurca al modo correcto."""
    try:
        params = BufferParams(**parameters)
    except Exception as exc:  # pylint: disable=broad-except
        return tool_error(OPERATION, str(exc))

    if params.features:
        # CPU (Shapely): a un hilo para no bloquear el event loop
        return await asyncio.to_thread(_execute_in_memory, params)
    return await _execute_postgis(params)


async def _execute_postgis(params: BufferParams) -> Dict[str, Any]:
    payload = params.model_dump(exclude={"features"})
    result = await backend().call(OPERATION, PATH, payload)
    if not result.get("success", False):
        result.setdefault("source", "postgis")
        result.setdefault("table", params.table)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Modo In-Memory (features del frontend — archivos importados)
# ═══════════════════════════════════════════════════════════════════════════

def _execute_in_memory(params) -> dict:
    """
    Buffer sobre features GeoJSON usando Shapely + PyProj.

    1. Recibe features en WGS84 (EPSG:4326)
    2. Detecta zona UTM automáticamente desde el centroide
    3. Reproyecta a UTM → buffer en metros → reproyecta a WGS84
    """
    try:
        import pyproj
        from shapely.geometry import mapping, shape
        from shapely.ops import transform, unary_union
    except ImportError as e:
        return {"type": "error", "operation": "buffer_analysis",
                "error": f"Dependencia faltante: {e}. pip install shapely pyproj",
                "success": False}

    distance_m = params.distance * _UNIT_TO_METERS[params.unit]
    source_name = params.table or "archivo_importado"
    features_in = params.features

    if not features_in:
        return {"type": "error", "operation": "buffer_analysis",
                "error": "No se recibieron features para procesar.", "success": False}

    try:
        # ── Centroide promedio para zona UTM ──────────────────────────
        coords = []
        for f in features_in:
            geom_data = f.get("geometry")
            if geom_data:
                centroid = shape(geom_data).centroid
                coords.append((centroid.x, centroid.y))

        if not coords:
            return {"type": "error", "operation": "buffer_analysis",
                    "error": "No se encontraron geometrías válidas.", "success": False}

        avg_lon = sum(c[0] for c in coords) / len(coords)
        avg_lat = sum(c[1] for c in coords) / len(coords)

        # ── Zona UTM automática ──────────────────────────────────────
        utm_zone = int((avg_lon + 180) / 6) + 1
        utm_epsg = (32600 + utm_zone) if avg_lat >= 0 else (32700 + utm_zone)

        project_to_utm = pyproj.Transformer.from_crs(
            "EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True
        ).transform
        project_to_wgs84 = pyproj.Transformer.from_crs(
            f"EPSG:{utm_epsg}", "EPSG:4326", always_xy=True
        ).transform

        # ── Buffer por feature ───────────────────────────────────────
        buffered_features = []
        for f in features_in:
            geom_data = f.get("geometry")
            if not geom_data:
                continue

            geom_wgs84 = shape(geom_data)
            geom_utm = transform(project_to_utm, geom_wgs84)
            buffered_utm = geom_utm.buffer(distance_m)
            buffered_wgs84 = transform(project_to_wgs84, buffered_utm)

            props = dict(f.get("properties", {}) or {})
            props.update({
                "buffer_distance": params.distance,
                "buffer_unit": params.unit,
                "source_layer": source_name,
            })

            buffered_features.append({
                "type": "Feature",
                "geometry": mapping(buffered_wgs84),
                "properties": props,
            })

        # ── Dissolve opcional ────────────────────────────────────────
        if params.dissolve and len(buffered_features) > 1:
            all_geoms = [shape(f["geometry"]) for f in buffered_features]
            merged = unary_union(all_geoms)
            buffered_features = [{
                "type": "Feature",
                "geometry": mapping(merged),
                "properties": {
                    "buffer_distance": params.distance,
                    "buffer_unit": params.unit,
                    "source_layer": source_name,
                    "dissolved": True,
                    "original_count": len(all_geoms),
                },
            }]

        geojson = {"type": "FeatureCollection", "features": buffered_features}

        logger.info(
            "[buffer_skill] Buffer in-memory %.1f %s sobre '%s': %d features.",
            params.distance, params.unit, source_name, len(buffered_features),
        )

        return {
            "type": "analysis_result", "operation": "buffer_analysis",
            "source": "in_memory", "table": source_name,
            "distance": params.distance, "unit": params.unit,
            "count": len(buffered_features), "geojson": geojson, "success": True,
        }

    except Exception as exc:
        logger.error("[buffer_skill] Error buffer in-memory: %s", exc, exc_info=True)
        return {"type": "error", "operation": "buffer_analysis",
                "source": "in_memory", "error": str(exc), "success": False}

