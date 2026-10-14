"""
Port: ILLMProvider
===================
Interfaz abstracta genérica para proveedores de modelos de lenguaje.
Permite cambiar entre Azure OpenAI, Azure AI Foundry, OpenAI, Kimi (Moonshot),
DeepSeek, Groq, OpenRouter, Ollama, vLLM, etc. sin tocar la lógica del agente.

Mismo patrón que ILayerSource: el agente depende de esta interfaz y el
DI Container decide el adaptador según LLM_PROVIDER.

Formato canónico de mensajes
-----------------------------
Se adopta el formato de `chat.completions` de OpenAI como contrato del port,
porque es el que hablan la mayoría de proveedores:

    {"role": "system" | "user" | "assistant" | "tool", "content": str, ...}

Un adaptador cuya API nativa use otro formato (p. ej. Anthropic Messages) es
responsable de traducir en ambos sentidos dentro de su implementación.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMToolCall:
    """Invocación de herramienta normalizada, independiente del proveedor."""

    id: str
    name: str
    arguments: str  # JSON serializado tal cual lo devolvió el modelo


@dataclass
class LLMResponse:
    """Respuesta normalizada de un LLM."""

    content: Optional[str] = None
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    model: Optional[str] = None
    # Razonamiento del modelo (OpenRouter). Hay que devolverlo SIN modificar en
    # el mensaje assistant del turno siguiente para que continúe donde lo dejó.
    reasoning_details: Any = None
    raw: Any = None  # Respuesta cruda del SDK, por si se necesita depurar
    # Consumo y latencia de la llamada, para observabilidad y costo. None si
    # el proveedor no informa `usage` (algunos compatibles OpenAI no lo hacen).
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ILLMProvider(ABC):
    """Interfaz genérica para cualquier proveedor de LLM con function calling"""

    @abstractmethod
    def get_provider_type(self) -> str:
        """
        Tipo de adaptador: 'azure_openai', 'azure_foundry', 'azure_agent',
        'openai_compatible' — o 'misconfigured' si la configuración no era
        válida y la factory devolvió el adaptador nulo.
        """
        ...

    @abstractmethod
    def get_model(self) -> str:
        """
        Identificador del modelo que se enviará en la petición.
        En Azure es el nombre del *deployment*, no el del modelo.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        True si el cliente pudo construirse. Intenta conectar una vez si aún
        no lo ha hecho — nunca lanza excepción.
        """
        ...

    @abstractmethod
    def get_last_error(self) -> Optional[str]:
        """Último error de conexión o de llamada, para mensajes de diagnóstico"""
        ...

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """
        Envía una conversación al modelo y devuelve la respuesta normalizada.

        Args:
            messages:    Historial en formato canónico (ver docstring del módulo).
            tools:       Definiciones de funciones. None = sin function calling.
            tool_choice: "auto" | "none" | "required". Ignorado si tools es None.
            timeout:     Segundos. None = el timeout configurado del proveedor.

        Raises:
            RuntimeError:  El proveedor no está disponible (sin credenciales, etc.).
            TimeoutError:  El modelo no respondió dentro del timeout.
            Exception:     Errores propios del SDK del proveedor.
        """
        ...

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """
        Versión async de `chat`, la que usa el agente.

        Por defecto ejecuta `chat` en un hilo para que cualquier adaptador
        funcione sin cambios; los que tienen cliente async nativo (todos los
        de OpenAI/Azure, vía OpenAIChatProviderBase) la sobreescriben para que
        el timeout cancele de verdad la petición.
        """
        return await asyncio.to_thread(self.chat, messages, tools, tool_choice, timeout)

    async def aclose(self) -> None:
        """Libera clientes async del proveedor. Por defecto no hay nada que cerrar."""
