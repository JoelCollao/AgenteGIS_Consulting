"""
Adaptador: Azure AI Foundry (Entra ID, sin API key)
====================================================
Autentica con DefaultAzureCredential: Managed Identity cuando la app corre en
Azure (Container Apps), `az login` en desarrollo local. Es el modo sin secretos
y por tanto NO funciona fuera de Azure — allí usa LLM_PROVIDER=azure.

Variables:
    AZURE_PROJECT_ENDPOINT    https://<recurso>.services.ai.azure.com/api/projects/<proyecto>
    AZURE_TENANT_ID           Tenant de Entra ID (opcional, para multi-tenant)
    AZURE_OPENAI_DEPLOYMENT   Nombre del deployment
"""

import logging
from typing import Any

from agent_service.models.base import OpenAIChatProviderBase

logger = logging.getLogger(__name__)


class AzureFoundryProvider(OpenAIChatProviderBase):
    """Azure AI Foundry autenticado con credential de Entra ID"""

    # Obtener el token puede tardar (IMDS inalcanzable fuera de Azure, 'az
    # login' en frío…). El request de usuario espera como mucho esto — ver
    # OpenAIChatProviderBase._ensure_client, que adopta el intento en vez de
    # descartarlo si se agota.
    _CONNECT_TIMEOUT_SECONDS = 15

    def get_provider_type(self) -> str:
        return "azure_foundry"

    def _build_client(self) -> Any:
        """
        Se ejecuta en el hilo en segundo plano de `_ensure_client`: puede
        bloquear sin miedo.
        """
        cfg = self._config

        if not cfg.project_endpoint:
            raise ValueError(
                "Falta AZURE_PROJECT_ENDPOINT "
                "(https://<recurso>.services.ai.azure.com/api/projects/<proyecto>)."
            )

        logger.info("[LLM] Conectando a Azure AI Foundry (credential)...")

        from azure.ai.projects import AIProjectClient

        from agent_service.models.entra import build_credential

        credential = build_credential(cfg.tenant_id)
        credential.get_token("https://cognitiveservices.azure.com/.default")

        client = AIProjectClient(endpoint=cfg.project_endpoint, credential=credential)
        return client.get_openai_client()
