"""
LangGraphOrchestrator - Motor de flujo multiagente GIS + IA
===========================================================
Orquestación con LangGraph y routing condicional.

Grafo:
  START → IntentRouter → (condicional)
    ├── "map_context" → MapContextAgent → END
    └── "gis_agent"   → GISAgent (function calling) → Validator → END
"""

import inspect
import logging
import time
from typing import Any, Callable, Dict

from langgraph.graph import END, START, StateGraph

from agent_service.orchestrator.agents_wrappers import (
    GISAgent,
    IntentRouterAgent,
    MapContextAgent,
    ValidatorAgent,
)
from agent_service.orchestrator.state import MultiAgentState
from agent_service.telemetry.observability import span

logger = logging.getLogger(__name__)


def _traced(name: str, node: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """
    Envuelve un nodo en un span `agent.node <name>` (hijo del de la request).
    El envoltorio es async: el grafo se ejecuta con `ainvoke` y los nodos
    que esperan al LLM (GISAgent) son corrutinas; los demás son síncronos y
    triviales (regex, lectura de dicts), así que se llaman directamente.
    """

    async def _run(state):
        with span(f"agent.node {name}", **{"langgraph.node": name}):
            result = node(state)
            if inspect.isawaitable(result):
                result = await result
            return result

    return _run


class LangGraphOrchestrator:
    """Orquestador multiagente GIS + IA con routing condicional"""

    def __init__(self):
        self.agents = {
            "intent_router": IntentRouterAgent(),
            "map_context": MapContextAgent(),
            "gis_agent": GISAgent(),
            "validator": ValidatorAgent(),
        }
        self.graph = self._build_graph().compile()

    def _build_graph(self):
        graph = StateGraph(MultiAgentState)

        # Nodos
        graph.add_node("intent_router", _traced("intent_router", self.agents["intent_router"]))
        graph.add_node("map_context", _traced("map_context", self.agents["map_context"]))
        graph.add_node("gis_agent", _traced("gis_agent", self.agents["gis_agent"]))
        graph.add_node("validator", _traced("validator", self.agents["validator"]))

        # Flujo: START → IntentRouter
        graph.add_edge(START, "intent_router")

        # Routing condicional basado en el plan del IntentRouter
        graph.add_conditional_edges(
            "intent_router",
            self._route_by_plan,
            {
                "map_context": "map_context",
                "gis_agent": "gis_agent",
            },
        )

        # map_context → END (respuesta directa sin validación)
        graph.add_edge("map_context", END)

        # gis_agent → validator → END
        graph.add_edge("gis_agent", "validator")
        graph.add_edge("validator", END)

        return graph

    @staticmethod
    def _route_by_plan(state: MultiAgentState) -> str:
        """Determina la rama del grafo según el plan del IntentRouter."""
        plan = state.result.get("plan", "gis_agent")
        if plan == "map_context":
            return "map_context"
        return "gis_agent"

    async def arun(self, input_data: Dict) -> Dict:
        """Ejecuta el flujo completo y retorna respuesta estructurada."""
        start_time = time.time()
        logger.info("Iniciando flujo LangGraph multiagente...")

        if "result" not in input_data:
            input_data["result"] = {}
        if "conversation_history" not in input_data:
            input_data["conversation_history"] = []
        if "session_id" not in input_data:
            input_data["session_id"] = ""

        with span(
            "invoke_agent gis_orchestrator",
            **{
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "gis_orchestrator",
            },
        ) as root:
            result = await self.graph.ainvoke(input_data)
            # Con un dataclass como estado, invoke devuelve un dict de canales
            plan = (result.get("result") or {}).get("plan") if isinstance(result, dict) else None
            if plan:
                root.set_attribute("langgraph.plan", plan)

        # Convertir resultado a dict
        if hasattr(result, "__dict__"):
            from dataclasses import asdict, is_dataclass

            if is_dataclass(result):
                output = asdict(result)
            else:
                output = vars(result)
        elif isinstance(result, dict):
            output = result
        else:
            output = {"result": {}}

        result_data = (
            output.get("result", {}) if isinstance(output.get("result"), dict) else {}
        )
        reply = result_data.get("reply", "")
        source = result_data.get("source", "fallback")
        intent = result_data.get("intent", "conversational")
        confidence = result_data.get("confidence", 0.8)
        agent_type = result_data.get("agent_type", "conversational")

        if not reply:
            reply = "No se pudo generar una respuesta."
            source = "fallback"

        processing_time_ms = round((time.time() - start_time) * 1000, 2)

        response = {
            "agent_type": agent_type,
            "metadata": {
                "confidence": confidence,
                "intent": intent,
                "orchestrator_version": "4.0.0",
                "processing_time_ms": processing_time_ms,
            },
            "reply": reply,
            "source": source,
            "success": result_data.get("success", True),
        }

        # Propagar campos extra generados por skills (buffer, filtro, etc.)
        for extra_key in (
            "agent", "bufferGeojson", "filteredGeojson", "newLayerId", "layerName",
            "tools_executed",
        ):
            if extra_key in result_data:
                response[extra_key] = result_data[extra_key]

        # Alias "result" requerido por el frontend cuando la skill produce una capa nueva
        if result_data.get("agent") in ("AgenteBuffer", "AgenteFiltro"):
            response["result"] = reply

        logger.info("Reply: %s... | Source: %s", reply[:80], source)
        return response


# Instancia global (singleton)
orchestrator = LangGraphOrchestrator()
