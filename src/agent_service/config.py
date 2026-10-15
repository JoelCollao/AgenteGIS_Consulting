"""
Configuración de agent-service desde variables de entorno
==========================================================
Solo lo que el agente necesita. Aquí NO hay cadena de conexión a base de datos
ni a Redis: los datos se piden al backend por HTTP (`BACKEND_URL`).

La configuración del proveedor de LLM vive aparte, en
`agent_service.models.llm_settings`, con el mismo esquema de variables que
tenía el backend (LLM_PROVIDER, AZURE_*, LLM_API_KEY…).
"""

import os
from dataclasses import dataclass, field
from typing import List


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw == "true"


def _env_list(name: str) -> List[str]:
    return [v.strip() for v in (os.getenv(name) or "").split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    environment: str = "production"
    port: int = 8001
    cors_allowed_origins: List[str] = field(default_factory=list)
    debug_errors: bool = False
    # Loguea el JSON que llega del frontend y el que se le responde (resumido)
    log_payloads: bool = False

    # Backend (único origen de datos)
    backend_url: str = "http://localhost:5000"
    backend_connect_timeout: float = 5.0
    backend_read_timeout: float = 30.0
    backend_max_connections: int = 20

    # JWT emitido por el backend: aquí solo se valida (firma, exp, iss)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "gis-backend-app"

    # Políticas de ejecución de tools
    policies_file: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment != "development"


def load_settings() -> Settings:
    return Settings(
        environment=os.getenv("ENVIRONMENT", "production"),
        port=int(os.getenv("PORT", "8001")),
        cors_allowed_origins=_env_list("CORS_ALLOWED_ORIGINS"),
        debug_errors=_env_bool("DEBUG_ERRORS", False),
        log_payloads=_env_bool("LOG_PAYLOADS", False),
        backend_url=(os.getenv("BACKEND_URL") or "http://localhost:5000").rstrip("/"),
        backend_connect_timeout=float(os.getenv("BACKEND_CONNECT_TIMEOUT", "5")),
        backend_read_timeout=float(os.getenv("BACKEND_READ_TIMEOUT", "30")),
        backend_max_connections=int(os.getenv("BACKEND_MAX_CONNECTIONS", "20")),
        jwt_secret=(os.getenv("JWT_SECRET") or "").strip(),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        jwt_issuer=os.getenv("JWT_ISSUER", "gis-backend-app"),
        policies_file=os.getenv("POLICIES_FILE", ""),
    )


settings = load_settings()
