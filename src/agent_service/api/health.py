"""Liveness y readiness de agent-service (públicos, sin JWT)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness: el proceso responde, sin mirar dependencias."""
    return {"status": "healthy", "service": "agent-service"}


@router.get("/ready")
async def readiness_check():
    """Readiness: proveedor LLM configurado. El backend se comprueba en cada tool."""
    from agent_service.models import default_llm_provider

    provider = default_llm_provider()
    if provider.get_provider_type() == "misconfigured":
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "llm": "misconfigured", "error": provider.get_last_error()},
        )
    return {"status": "ready", "llm": provider.get_provider_type()}
