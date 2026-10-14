"""
Motor de políticas de tools
============================
Lee `config/policies.yaml` y decide, por nombre de tool, si se ejecuta sola
(`auto`), si necesita aprobación (`approval`) o si está prohibida (`blocked`).

El registro de tools consulta `decide()` antes de ejecutar nada.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

AUTO = "auto"
APPROVAL = "approval"
BLOCKED = "blocked"
_MODES = {AUTO, APPROVAL, BLOCKED}

DEFAULT_FILE = Path(__file__).resolve().parents[3] / "config" / "policies.yaml"


@dataclass(frozen=True)
class PolicyEngine:
    default: str = BLOCKED
    tools: Dict[str, str] = field(default_factory=dict)

    def decide(self, tool_name: str) -> str:
        return self.tools.get(tool_name, self.default)

    @classmethod
    def from_dict(cls, data: dict) -> "PolicyEngine":
        default = str(data.get("default", BLOCKED)).strip().lower()
        if default not in _MODES:
            raise ValueError(f"policies: modo por defecto no válido '{default}'")
        tools: Dict[str, str] = {}
        for name, mode in (data.get("tools") or {}).items():
            mode = str(mode).strip().lower()
            if mode not in _MODES:
                raise ValueError(f"policies: modo no válido '{mode}' para la tool '{name}'")
            tools[str(name)] = mode
        return cls(default=default, tools=tools)

    @classmethod
    def from_file(cls, path: Optional[str] = None) -> "PolicyEngine":
        file_path = Path(path) if path else DEFAULT_FILE
        with file_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        engine = cls.from_dict(data)
        logger.info("[policies] %d tools con política (default=%s) desde %s",
                    len(engine.tools), engine.default, file_path)
        return engine


_engine: Optional[PolicyEngine] = None


def set_policy_engine(engine: Optional[PolicyEngine]) -> None:
    global _engine  # pylint: disable=global-statement
    _engine = engine


def policy_engine() -> PolicyEngine:
    """El motor activo. Si main.py no fijó uno, se carga el archivo por defecto."""
    global _engine  # pylint: disable=global-statement
    if _engine is None:
        _engine = PolicyEngine.from_file()
    return _engine
