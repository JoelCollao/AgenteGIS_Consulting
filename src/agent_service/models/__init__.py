"""
Adaptadores de LLM (implementaciones de ILLMProvider)
======================================================
El DI Container elige uno según LLM_PROVIDER. Ver src/config/llm_settings.py.
"""

import logging
from typing import Optional

from agent_service.models.llm_settings import (
    KIND_AZURE_AGENT,
    KIND_AZURE_FOUNDRY,
    KIND_AZURE_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    LLMConfig,
    load_llm_config,
)
from agent_service.models.illm_provider import ILLMProvider
from agent_service.models.azure_agent import AzureAgentProvider
from agent_service.models.azure_foundry import AzureFoundryProvider
from agent_service.models.azure_openai import AzureOpenAIProvider
from agent_service.models.misconfigured import MisconfiguredProvider
from agent_service.models.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

_ADAPTERS = {
    KIND_AZURE_OPENAI: AzureOpenAIProvider,
    KIND_AZURE_FOUNDRY: AzureFoundryProvider,
    KIND_AZURE_AGENT: AzureAgentProvider,
    KIND_OPENAI_COMPATIBLE: OpenAICompatibleProvider,
}


def build_llm_provider(config: Optional[LLMConfig] = None) -> ILLMProvider:
    """
    Factory: instancia el adaptador que corresponde a la configuración.
    No conecta todavía — la conexión es perezosa, en la primera consulta.

    Nunca lanza: una configuración inválida devuelve MisconfiguredProvider
    para que la API siga levantando (las capas y el geoprocesamiento no
    dependen del LLM). El error se registra y viaja hasta la respuesta.
    """
    try:
        config = config or load_llm_config()
    except ValueError as exc:
        logger.error("[LLM] Configuración inválida: %s", exc)
        return MisconfiguredProvider(str(exc))

    adapter = _ADAPTERS.get(config.kind)
    if adapter is None:
        error = (
            f"Tipo de proveedor LLM no soportado: '{config.kind}'. "
            f"Opciones válidas: {', '.join(sorted(_ADAPTERS))}"
        )
        logger.error("[LLM] %s", error)
        return MisconfiguredProvider(error)

    logger.info("[LLM] Proveedor configurado: %s", config.describe())
    return adapter(config)


# Singleton del proceso. Sustituye a `container.llm_provider()` del backend:
# la primera llamada construye el adaptador según LLM_PROVIDER.
_default_provider: Optional[ILLMProvider] = None


def default_llm_provider() -> ILLMProvider:
    global _default_provider  # pylint: disable=global-statement
    if _default_provider is None:
        _default_provider = build_llm_provider()
    return _default_provider


__all__ = [
    "build_llm_provider",
    "default_llm_provider",
    "AzureOpenAIProvider",
    "AzureFoundryProvider",
    "AzureAgentProvider",
    "OpenAICompatibleProvider",
    "MisconfiguredProvider",
]
