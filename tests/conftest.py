"""
Fixtures comunes de agent-service
==================================
· Política permisiva por defecto: los tests registran tools falsas
  (`fake_tool`) que en producción quedarían bloqueadas por `default: blocked`.
  Los tests de políticas fijan su propio motor.
· Ningún test toca red: el BackendClient se construye con httpx.MockTransport.
"""

import os

import pytest

# Antes de importar agent_service: config.settings se lee al importar
os.environ.setdefault("JWT_SECRET", "test-secret-de-al-menos-32-caracteres-xx")
os.environ.setdefault("ENVIRONMENT", "development")

from agent_service.policies.engine import AUTO, PolicyEngine, set_policy_engine  # noqa: E402
from agent_service.tools.base import set_backend_client  # noqa: E402


@pytest.fixture(autouse=True)
def _politica_permisiva():
    set_policy_engine(PolicyEngine(default=AUTO))
    yield
    set_policy_engine(None)
    set_backend_client(None)
