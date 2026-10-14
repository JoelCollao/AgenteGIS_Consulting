"""
Endpoints /api/ai/* — DEPRECADOS
=================================
Movidos sin cambios desde `src/presentation/api/routes.py` del backend. El
frontend no los usa (verificado en la Fase 1); se marcan `deprecated` en
OpenAPI (decisión P4) y se eliminarán en una versión posterior.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent_service.orchestrator.langgraph_flow import orchestrator
from agent_service.api.schemas import AIRequest

router = APIRouter()


@router.post("/ai/gis-query", deprecated=True)
async def ai_gis_query(data: AIRequest):
    response = await orchestrator.arun(
        {"message": data.message, "context": data.context, "type": "gis-query"}
    )
    return response


@router.post("/ai/report", deprecated=True)
async def ai_report(data: AIRequest):
    response = await orchestrator.arun(
        {"message": data.message, "context": data.context, "type": "report"}
    )
    return response


@router.post("/ai/chat", deprecated=True)
async def ai_chat(data: AIRequest):
    response = await orchestrator.arun(
        {"message": data.message, "context": data.context}
    )
    return response
