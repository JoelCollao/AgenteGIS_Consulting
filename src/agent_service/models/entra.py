"""
Credencial de Entra ID compartida por los adaptadores de Azure
===============================================================
`azure_foundry` y `azure_agent` autentican igual, así que construyen la
credencial aquí en vez de repetirla.

Por qué se descarta Managed Identity fuera de Azure
----------------------------------------------------
`DefaultAzureCredential` prueba los credentials en orden y Managed Identity va
antes que la CLI. En una máquina de desarrollo eso significa sondear el
endpoint de IMDS (169.254.169.254), una IP que no responde y donde el intento
se agota tras varios segundos. El token de `az login` acaba llegando, pero para
entonces se ha consumido casi todo el presupuesto de conexión y el arranque
queda a merced de una carrera.

Detectar el entorno y saltarse ese eslabón cuando no aplica hace que en local
la conexión sea inmediata, sin renunciar a Managed Identity en producción.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Variables que solo existen cuando el proceso corre DENTRO de Azure. Las
# inyecta la plataforma, nunca un .env de desarrollo.
_AZURE_RUNTIME_VARS = (
    "IDENTITY_ENDPOINT",  # Container Apps, App Service, Functions
    "MSI_ENDPOINT",  # App Service antiguo
    "CONTAINER_APP_NAME",  # Container Apps
    "WEBSITE_INSTANCE_ID",  # App Service
    "AZURE_FEDERATED_TOKEN_FILE",  # Workload Identity en AKS
)


def running_in_azure() -> bool:
    """True si el proceso corre en una plataforma de Azure con identidad."""
    return any(os.getenv(var) for var in _AZURE_RUNTIME_VARS)


def build_credential(tenant_id: Optional[str] = None) -> Any:
    """
    DefaultAzureCredential ajustado al entorno.

    Args:
        tenant_id: Tenant permitido además del propio del credential. OJO: no
                   dirige el login de la CLI — `AzureCliCredential` toma el
                   tenant de la suscripción activa de `az account show`, y
                   DefaultAzureCredential no le reenvía este valor.
    """
    from azure.identity import DefaultAzureCredential

    in_azure = running_in_azure()
    if not in_azure:
        logger.info(
            "[LLM] Fuera de Azure: se omite Managed Identity y se usa la "
            "credencial de 'az login'."
        )

    return DefaultAzureCredential(
        exclude_developer_cli_credential=True,
        exclude_managed_identity_credential=not in_azure,
        additionally_allowed_tenants=([tenant_id] if tenant_id else []),
    )
