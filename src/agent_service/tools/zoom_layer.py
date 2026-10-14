"""
zoom_layer_skill - Ajuste de Encuadre del Mapa a una Capa
==========================================================
Genera una instrucción de acción para el frontend indicando que el visor
cartográfico debe ajustar el zoom y encuadre para mostrar una capa completa
o un extent específico. No accede a la base de datos.

Parámetros esperados:
    layer  (str, requerido):         Nombre de la capa a enfocar.
    extent (list[float], opcional):  [minx, miny, maxx, maxy] en WGS84.
                                     Si está vacío, el frontend calcula el extent.
    zoom   (int, opcional):          Nivel de zoom objetivo (0-20). Default None.

Retorna:
    {
        "type":      "map_action",
        "operation": "zoom_layer",
        "action":    "zoom",
        "layer":     str,
        "extent":    list[float] | None,
        "zoom":      int | None,
        "message":   str,
        "success":   bool
    }
"""

import logging

logger = logging.getLogger(__name__)


def execute(parameters: dict) -> dict:
    """
    Genera la instrucción de zoom/encuadre para el visor cartográfico.

    No requiere acceso a base de datos; retorna directamente un payload
    de acción de mapa que el frontend debe interpretar para ajustar
    la vista del visor a la capa indicada.

    Args:
        parameters: Diccionario con los parámetros descritos en el módulo.

    Returns:
        Diccionario con la instrucción de acción de mapa.
    """
    layer = str(parameters.get("layer", "")).strip()
    if not layer:
        return {
            "type": "error", "operation": "zoom_layer",
            "error": "El parámetro 'layer' es requerido.", "success": False,
        }

    # Extent opcional: [minx, miny, maxx, maxy]
    extent = None
    raw_extent = parameters.get("extent")
    if raw_extent and isinstance(raw_extent, (list, tuple)) and len(raw_extent) == 4:
        try:
            extent = [float(v) for v in raw_extent]
            # Validar rango básico WGS84
            if not (-180 <= extent[0] <= 180 and -180 <= extent[2] <= 180
                    and -90 <= extent[1] <= 90 and -90 <= extent[3] <= 90):
                extent = None
        except (TypeError, ValueError):
            extent = None

    # Nivel de zoom opcional
    zoom = None
    raw_zoom = parameters.get("zoom")
    if raw_zoom is not None:
        try:
            zoom = max(0, min(20, int(raw_zoom)))
        except (TypeError, ValueError):
            zoom = None

    # Mensaje descriptivo para el agente
    if extent:
        message = (
            f"El mapa se ajustará al encuadre {extent} de la capa '{layer}'."
        )
    else:
        message = f"El mapa se ajustará automáticamente a la extensión de la capa '{layer}'."

    logger.info("[zoom_layer_skill] %s", message)

    return {
        "type":      "map_action",
        "operation": "zoom_layer",
        "action":    "zoom",
        "layer":     layer,
        "extent":    extent,
        "zoom":      zoom,
        "message":   message,
        "success":   True,
    }
