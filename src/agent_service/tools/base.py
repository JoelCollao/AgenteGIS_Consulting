"""
Piezas comunes de las tools
============================
Una tool de agent-service:
  1. valida los argumentos con su modelo Pydantic de `tools/params.py` (copia
     de las reglas del backend, que es quien manda: contrato §2);
  2. llama a su endpoint vía `clients/backend.py`;
  3. devuelve el dict resultante.

No importa nada del backend ni de un driver de base de datos (lo vigila
import-linter, ver pyproject.toml).
"""

from typing import Any, Dict, Optional

from agent_service.clients.backend import BackendClient

_backend: Optional[BackendClient] = None


def set_backend_client(client: Optional[BackendClient]) -> None:
    """Lo llama main.py al arrancar (y los tests con un MockTransport)."""
    global _backend  # pylint: disable=global-statement
    _backend = client


def backend() -> BackendClient:
    if _backend is None:
        raise RuntimeError("BackendClient no configurado: llama a set_backend_client() al arrancar")
    return _backend


def tool_error(operation: str, message: str, **extra: Any) -> Dict[str, Any]:
    """Mismo formato de error que devolvían las skills en el backend."""
    return {"type": "error", "operation": operation, "error": message, "success": False, **extra}
