"""
Costo estimado de una llamada al LLM
=====================================
El precio depende del contrato con el proveedor, así que no se codifica: se
configura por entorno, en USD por millón de tokens.

    LLM_PRICE_INPUT_PER_1M=0.40
    LLM_PRICE_OUTPUT_PER_1M=1.60

Sin esas variables, `estimate_cost_usd` devuelve None y el log `llm.call`
lleva `cost_usd: null` (no se inventa un número).
"""

import os
from typing import Optional


def _price(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def estimate_cost_usd(tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    price_in = _price("LLM_PRICE_INPUT_PER_1M")
    price_out = _price("LLM_PRICE_OUTPUT_PER_1M")
    if price_in is None or price_out is None or tokens_in is None or tokens_out is None:
        return None
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)
