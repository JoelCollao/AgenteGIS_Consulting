"""
Búsqueda de capas por nombre
============================
Normalización y coincidencia de nombres de capa compartidas por el agente GIS
y el nodo MapContext del grafo: "Parques_Lima", "parques-lima" y
"parques lima" son la misma capa.
"""

import re
from typing import Any, Iterable, Optional

_SEPARATORS = re.compile(r"[\s_\-]+")


def normalize_name(name: str) -> str:
    """Minúsculas y espacios/guiones/guiones bajos colapsados a un espacio."""
    return _SEPARATORS.sub(" ", name.lower()).strip()


def _layer_name(layer: Any) -> str:
    return normalize_name(layer.get("name", "") if isinstance(layer, dict) else str(layer))


def match_layer(hint: str, layers: Iterable[Any], *, hint_may_contain_name: bool = False) -> Optional[Any]:
    """
    Busca la capa que mejor coincide con `hint`, por orden de preferencia:

    1. nombre exacto (normalizado);
    2. el hint está contenido en el nombre ("parques" → "parques lima");
    3. con `hint_may_contain_name`, el nombre está contenido en el hint
       ("capa de parques lima 2024" → "parques lima").
    """
    if not hint:
        return None
    hint_norm = normalize_name(hint)
    layers = list(layers)

    for layer in layers:
        if _layer_name(layer) == hint_norm:
            return layer
    for layer in layers:
        if hint_norm in _layer_name(layer):
            return layer
    if hint_may_contain_name:
        for layer in layers:
            name = _layer_name(layer)
            if name and name in hint_norm:
                return layer
    return None
