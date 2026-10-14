"""
Prompts del agente, fuera del código
=====================================
Antes vivían como literales en `src/application/agents/system_prompts.py` del
backend. Ahora son archivos de datos junto a este módulo:

  · gis_system.md   → GIS_SYSTEM_PROMPT (texto exacto, sin reformatear)
  · gis_tools.json  → GIS_TOOL_DEFINITIONS (los esquemas de function calling)

Se exponen con los mismos nombres para que quien los importaba no cambie.
`scripts/sync_agent_tools.py` publica GIS_TOOL_DEFINITIONS en el agente de
Azure AI Foundry, así que el JSON es también la fuente de verdad del portal.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Contenido de `<name>.md`, tal cual."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def load_tool_definitions(name: str) -> List[Dict[str, Any]]:
    """Lista de tools de `<name>.json` en el formato de chat.completions."""
    return json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))


GIS_SYSTEM_PROMPT: str = load_prompt("gis_system")
GIS_TOOL_DEFINITIONS: List[Dict[str, Any]] = load_tool_definitions("gis_tools")
