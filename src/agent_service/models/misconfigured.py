"""
Adaptador nulo para configuración inválida
===========================================
Si LLM_PROVIDER apunta a un preset inexistente o falta el modelo, la API
debe seguir levantando: las capas, el geoprocesamiento y /health no dependen
del LLM, y un contenedor en crash-loop es mucho más difícil de diagnosticar
que un endpoint que responde explicando el problema.

El agente ve un proveedor "no disponible" y devuelve su respuesta de
fallback con el motivo exacto.
"""

from typing import Any, Dict, List, Optional

from agent_service.models.illm_provider import ILLMProvider, LLMResponse


class MisconfiguredProvider(ILLMProvider):
    """Proveedor que solo sabe explicar por qué no se pudo configurar"""

    def __init__(self, error: str):
        self._error = error

    def get_provider_type(self) -> str:
        return "misconfigured"

    def get_model(self) -> str:
        return "(sin configurar)"

    def is_available(self) -> bool:
        return False

    def get_last_error(self) -> Optional[str]:
        return self._error

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        raise RuntimeError(f"Proveedor de LLM mal configurado: {self._error}")
