"""
agent-service — punto de entrada
=================================
Cablea las dependencias y expone la API del agente:

  POST /api/v1/orchestrator         chat principal (lo usa el frontend)
  GET  /api/v1/health, /api/v1/ready

Dependencias que se construyen aquí:
  · BackendClient (httpx) → tools/base.set_backend_client
  · PolicyEngine (config/policies.yaml) → policies.engine.set_policy_engine
  · Proveedor LLM (singleton de agent_service.models), precalentado en el lifespan

Ejecutar: uvicorn agent_service.main:app --port 8001
"""

import json
import logging
import os
import sys
import threading
import time as _time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Antes de leer cualquier variable. En Container Apps no hay .env y
# load_dotenv nunca pisa variables ya presentes en el entorno.
load_dotenv()

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from agent_service.config import settings  # noqa: E402
from agent_service.telemetry.observability import (  # noqa: E402
    configure_logging,
    configure_telemetry,
    request_id_var,
)

configure_logging(json_logs=settings.is_production)
logger = logging.getLogger(__name__)

# Mismas claves que redacta el backend antes de loguear un body (duplicado, P1)
_SENSITIVE_BODY_KEYS = {
    "password", "contrasena", "contraseña", "old_password", "new_password",
    "token", "access_token", "refresh_token", "authorization",
    "secret", "api_key", "apikey", "client_secret",
}


def _redactar_body(data):
    if isinstance(data, dict):
        return {
            k: ("***" if k.lower() in _SENSITIVE_BODY_KEYS else _redactar_body(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redactar_body(v) for v in data]
    return data


# ── Dependencias del proceso ─────────────────────────────────────────────
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Arranque y cierre en un solo sitio; el `finally` garantiza el cierre."""
    from agent_service.clients.backend import BackendClient
    from agent_service.policies.engine import PolicyEngine, set_policy_engine
    from agent_service.tools.base import set_backend_client

    set_backend_client(
        BackendClient(
            settings.backend_url,
            connect_timeout=settings.backend_connect_timeout,
            read_timeout=settings.backend_read_timeout,
            max_connections=settings.backend_max_connections,
        )
    )
    set_policy_engine(PolicyEngine.from_file(settings.policies_file or None))
    logger.info("Backend: %s", settings.backend_url)

    # Warm-up del proveedor LLM en un hilo (movido desde app.py del
    # backend): las credenciales de Entra ID pueden tardar en frío.
    def _connect():
        try:
            from agent_service.models import default_llm_provider

            provider = default_llm_provider()
            if provider.is_available():
                logger.info("Proveedor LLM listo: %s", provider.get_provider_type())
            else:
                logger.warning("Proveedor LLM no disponible todavía (%s)", provider.get_last_error())
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("No se pudo precalentar el proveedor LLM: %s", exc)

    threading.Thread(target=_connect, daemon=True).start()

    try:
        yield
    finally:
        from agent_service.tools.base import backend, set_backend_client

        try:
            await backend().aclose()
        except RuntimeError:
            pass
        set_backend_client(None)

        from agent_service.models import default_llm_provider

        try:
            await default_llm_provider().aclose()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("No se pudo cerrar el cliente LLM: %s", exc)


def create_app() -> FastAPI:
    app = FastAPI(
        title="GIS Agent Service",
        description="Orquestación de agentes de IA sobre la API del backend GIS",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    # ── CORS (mismo criterio que el backend) ─────────────────────────────
    cors_origins = list(settings.cors_allowed_origins)
    if not cors_origins and not settings.is_production:
        cors_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=bool(cors_origins) and cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # ── X-Request-ID + log de tráfico (duplicado del backend, P1) ────────
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        start_time = _time.perf_counter()
        method = request.method
        path = request.url.path

        body_info = None
        if not settings.is_production and method in ("POST", "PUT", "PATCH"):
            if "json" in request.headers.get("content-type", ""):
                try:
                    body = await request.body()
                    if body:
                        body_info = json.dumps(_redactar_body(json.loads(body)), ensure_ascii=False)[:120]
                except Exception:  # pylint: disable=broad-except
                    body_info = "(no parseable)"

        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((_time.perf_counter() - start_time) * 1000, 1)
            logger.info(
                "%s %s -> %s (%.1f ms)", method, path, status, duration_ms,
                extra={
                    "event": "http.request",
                    "http_method": method,
                    "http_path": path,
                    "http_status": status,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else None,
                    **({"body": body_info} if body_info else {}),
                },
            )
            request_id_var.reset(token)

    # Un solo formato de error para toda la API (401, 422, 500…)
    from agent_service.api.errors import register_error_handlers

    register_error_handlers(app)

    # ── Routers ──────────────────────────────────────────────────────────
    from agent_service.api import health, orchestrator
    from agent_service.api.auth import get_current_user, resolve_jwt_secret

    try:
        resolve_jwt_secret()
    except ValueError as e:
        logger.critical("Configuración JWT: %s", e)
        sys.exit(1)

    auth_required = [Depends(get_current_user)]
    app.include_router(health.router, prefix="/api/v1", tags=["Health"])
    app.include_router(orchestrator.router, prefix="/api/v1", tags=["Orchestrator"], dependencies=auth_required)

    try:
        if configure_telemetry(app):
            logger.info("Telemetría OpenTelemetry → Application Insights activa")
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("No se pudo activar la telemetría: %s", e)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", str(settings.port))), log_level="info")
