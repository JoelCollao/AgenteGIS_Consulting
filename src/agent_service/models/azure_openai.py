"""
Adaptador: Azure OpenAI (API key)
==================================
Recurso "Azure OpenAI" clásico o cuenta AI Services/Foundry consumida por su
endpoint OpenAI. Autentica con API key, así que funciona desde cualquier sitio
— incluido un hosting fuera de Azure, donde no hay Managed Identity.

Variables:
    AZURE_OPENAI_ENDPOINT     https://<recurso>.openai.azure.com/
                              (si falta, se deriva de AZURE_PROJECT_ENDPOINT)
    AZURE_OPENAI_API_KEY      Clave del recurso
    AZURE_OPENAI_DEPLOYMENT   Nombre del *deployment*, no del modelo
    OPENAI_API_VERSION        Versión de la API (default GA en llm_settings)
"""

from typing import Any

from agent_service.models.base import OpenAIChatProviderBase


class AzureOpenAIProvider(OpenAIChatProviderBase):
    """Azure OpenAI autenticado con API key"""

    def get_provider_type(self) -> str:
        return "azure_openai"

    def _build_client(self) -> Any:
        from openai import AzureOpenAI

        cfg = self._config

        if not cfg.endpoint:
            raise ValueError(
                "Falta el endpoint de Azure OpenAI. Define AZURE_OPENAI_ENDPOINT "
                "(https://<recurso>.openai.azure.com/) o AZURE_PROJECT_ENDPOINT."
            )
        if not cfg.api_key:
            raise ValueError(
                "Falta AZURE_OPENAI_API_KEY. Si quieres autenticar con Managed "
                "Identity o 'az login' en vez de con clave, usa LLM_PROVIDER=azure_foundry."
            )

        return AzureOpenAI(
            api_key=cfg.api_key,
            api_version=cfg.api_version,
            azure_endpoint=cfg.endpoint,
            timeout=cfg.timeout,
        )

    def _build_async_client(self, sync_client: Any) -> Any:
        """
        AsyncAzureOpenAI desde la configuración: el cliente Azure añade
        `api-version` y la cabecera `api-key` a su manera, así que derivarlo
        del síncrono como hace la base no serviría.
        """
        from openai import AsyncAzureOpenAI

        cfg = self._config
        return AsyncAzureOpenAI(
            api_key=cfg.api_key,
            api_version=cfg.api_version,
            azure_endpoint=cfg.endpoint,
            timeout=cfg.timeout,
        )
