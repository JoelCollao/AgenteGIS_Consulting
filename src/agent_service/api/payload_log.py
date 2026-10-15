"""
Log del JSON de entrada y salida del orquestador (LOG_PAYLOADS=true)
=====================================================================
Para depurar el contrato con el frontend. Apagado por defecto: el cuerpo
puede traer capas enteras y datos de los usuarios.

No se vuelca tal cual: las geometrías se sustituyen por su tipo, las listas
largas se recortan a unos pocos elementos más un contador y los textos
largos se truncan. Así se ve la FORMA del JSON sin inundar el log.
"""

import json
import logging
from typing import Any

from agent_service.config import settings

logger = logging.getLogger("agent_service.payload")

_MAX_ITEMS = 3
_MAX_STR = 300


def _summarize(value: Any, key: str = "") -> Any:
    if key == "coordinates":
        return "<coordenadas>"
    if isinstance(value, dict):
        return {k: _summarize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [_summarize(v) for v in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"<… {len(value) - _MAX_ITEMS} más, {len(value)} en total>")
        return items
    if isinstance(value, str) and len(value) > _MAX_STR:
        return f"{value[:_MAX_STR]}<… {len(value)} caracteres>"
    return value


def log_payload(direction: str, payload: Any) -> None:
    """direction: 'request' (frontend → agente) o 'response' (agente → frontend)."""
    if not settings.log_payloads:
        return
    logger.info(
        "[payload] %s:\n%s",
        direction,
        json.dumps(_summarize(payload), ensure_ascii=False, indent=2, default=str),
    )
