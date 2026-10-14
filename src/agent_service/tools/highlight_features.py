"""
highlight_features_skill - Resaltar Features en el Mapa
========================================================
Genera una instrucción de acción para el frontend indicando qué features
resaltar en el visor cartográfico. Esta skill no accede a la base de datos;
produce un payload de acción de mapa que el frontend interpreta.

Parámetros esperados:
    layer       (str, requerido):       Nombre de la capa.
    feature_ids (list[int], opcional):  IDs de features a resaltar.
                                        Si está vacío, resalta toda la capa.
    style       (dict, opcional):       Estilo de resaltado:
                                        {"color": str, "weight": int, "opacity": float}

Retorna:
    {
        "type":        "map_action",
        "operation":   "highlight_features",
        "action":      "highlight",
        "layer":       str,
        "feature_ids": list[int],
        "style":       dict,
        "message":     str,
        "success":     bool
    }
"""

import logging

logger = logging.getLogger(__name__)

_DEFAULT_STYLE = {
    "color":   "#FF6600",
    "weight":  3,
    "opacity": 0.9,
}


def execute(parameters: dict) -> dict:
    """
    Genera la instrucción de resaltado de features para el frontend.

    No requiere acceso a base de datos; retorna directamente un payload
    de acción de mapa que el frontend debe interpretar para resaltar
    los features indicados en el visor cartográfico.

    Args:
        parameters: Diccionario con los parámetros descritos en el módulo.

    Returns:
        Diccionario con la instrucción de acción de mapa.
    """
    layer = str(parameters.get("layer", "")).strip()
    if not layer:
        return {
            "type": "error", "operation": "highlight_features",
            "error": "El parámetro 'layer' es requerido.", "success": False,
        }

    # IDs de features a resaltar (lista de enteros)
    raw_ids = parameters.get("feature_ids", [])
    feature_ids = []
    if isinstance(raw_ids, list):
        for fid in raw_ids:
            try:
                feature_ids.append(int(fid))
            except (TypeError, ValueError):
                pass
    elif raw_ids:
        try:
            feature_ids = [int(raw_ids)]
        except (TypeError, ValueError):
            pass

    # Estilo de resaltado (merge con defaults)
    style_input = parameters.get("style", {})
    style = {**_DEFAULT_STYLE}
    if isinstance(style_input, dict):
        style.update({k: v for k, v in style_input.items() if k in _DEFAULT_STYLE})

    # Mensaje descriptivo para el agente
    if feature_ids:
        message = (
            f"Se resaltarán {len(feature_ids)} feature(s) "
            f"(IDs: {feature_ids[:5]}{'...' if len(feature_ids) > 5 else ''}) "
            f"en la capa '{layer}'."
        )
    else:
        message = f"Se resaltará toda la capa '{layer}'."

    logger.info("[highlight_features_skill] %s", message)

    return {
        "type":        "map_action",
        "operation":   "highlight_features",
        "action":      "highlight",
        "layer":       layer,
        "feature_ids": feature_ids,
        "style":       style,
        "message":     message,
        "success":     True,
    }
