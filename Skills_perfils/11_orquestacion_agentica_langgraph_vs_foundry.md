# Orquestación agéntica: LangGraph vs Microsoft Foundry
**Requisito(s) del perfil:** D1 — orquestación agéntica (LangGraph/LangChain).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Diseñar flujos multiagente con routing, estado compartido, persistencia, ciclos (agente ↔ tools), HITL, y saber cuándo usar un framework de código (LangGraph) frente a un servicio gestionado (Foundry Agent Service).

## 2. Qué tengo hoy (evidencia)
**LangGraph**
- `requirements.txt` → `langgraph==1.0.9` (sin LangChain).
- `src/application/orchestration/langgraph_flow.py` → `StateGraph(MultiAgentState)`, `add_conditional_edges("intent_router", _route_by_plan, ...)`, `graph.invoke` síncrono, sin checkpointer.
- `src/application/orchestration/agents_wrappers.py` → nodos: `IntentRouterAgent` (regex), `MapContextAgent` (sin LLM), `GISAgent` (function calling), `ValidatorAgent` (reply no vacío).
- Grafo lineal (DAG), sin ciclos: el bucle de tools no está en el grafo.

**Microsoft Foundry**
- `src/infrastructure/llm/azure_foundry.py` → `AIProjectClient(...).get_openai_client()` con Entra ID: modelo desplegado en Foundry.
- `src/infrastructure/llm/azure_agent.py` → consumo de un **agente publicado** en Foundry vía `project.get_openai_client(agent_name=...)` y **Responses API**; lee la definición (`project.agents.get(...).versions.latest`); tools gestionadas (file_search/azure_ai_search/bing) se ejecutan en el servicio, funciones GIS en mi backend.
- `scripts/sync_agent_tools.py` → publica versiones nuevas del agente con las funciones del código.
- No se usan `azure-ai-agents`, threads/runs clásicos ni tracing de Foundry (0 coincidencias de `create_agent`, `threads`, `runs`). Usas la API nueva (agents versionados + Responses), que es la dirección actual de Foundry.

## 3. Brechas
- Router no-LLM; sin subgrafos, ciclos, checkpointer, `interrupt`, `Command`, `Send` (map-reduce).
- Los dos mundos están en paralelo: Foundry Agent reemplaza el modelo dentro de `GISAgent`, pero no se integra como nodo de un grafo con otros agentes.
- Sin tracing ni evaluación de Foundry.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| AG1 | Reescribir el grafo como ciclo ReAct: `agent` ⇄ `tools` con `ToolNode`-style propio + `tools_condition` | `langgraph_flow.py` | Multi-paso funcionando | M |
| AG2 | Router con LLM + `with_structured_output`-equivalente (JSON schema Pydantic) y regex como atajo | `agents_wrappers.py` | Eval ≥ 90% | S |
| AG3 | Checkpointer Postgres + `interrupt` (ver 06 y 08) | — | — | M |
| AG4 | Nodo `foundry_rag_agent` que delega preguntas documentales a un agente Foundry con `file_search` (RAG gestionado) y vuelve al grafo | `agents_wrappers.py`, `azure_agent.py` | Respuesta con citas | M |
| AG5 | Tracing Foundry + Agent evaluators (`azure-ai-evaluation`: `IntentResolution`, `ToolCallAccuracy`, `TaskAdherence`) | `tests/evals/` | Reporte | M |
| AG6 | ADR "LangGraph vs Foundry Agent Service" en `docs/` | `docs/adr/001-orquestacion.md` | Documento | S |

## 5. Teoría que debo dominar para la entrevista
| Criterio | LangGraph | Foundry Agent Service |
|---|---|---|
| Control del flujo | Total (grafo explícito, ciclos, condiciones) | Bucle gestionado por el servicio |
| Estado/memoria | Checkpointer propio (Postgres/Redis) | Conversations/threads gestionados |
| Tools | Cualquier función Python | Funciones (las ejecuto yo) + gestionadas (file_search, AI Search, Bing, code interpreter, OpenAPI, MCP) |
| Seguridad/gobierno | La implemento yo | Entra ID, RBAC, Content Safety, red privada integrados |
| Observabilidad | LangSmith u OTel propio | Tracing nativo a App Insights |
| Portabilidad | Multi-proveedor | Azure |
| Cuándo | Lógica de negocio compleja, HITL, determinismo | Time-to-market, RAG gestionado, gobierno corporativo |

Patrón híbrido recomendado (y que mi código ya insinúa): **LangGraph como orquestador de negocio** + **agentes Foundry como nodos especializados** (RAG gestionado) + funciones de dominio en el backend.

Conceptos: supervisor vs swarm vs jerárquico; `Send` para fan-out; `Command` para saltar de nodo y actualizar estado; reducers de estado; HITL con `interrupt`; time travel con checkpoints.

## 6. Preguntas probables y respuesta guía
- *¿Por qué un router con regex?* Latencia y costo cero para intents triviales; para el resto el LLM decide con function calling. Lo mejoraría con un router LLM con regex como fast-path.
- *¿Qué ganas usando LangGraph si solo tienes un DAG?* Hoy poco; el valor real aparece con ciclos, checkpointing e interrupt (AG1, AG3).
- *¿Cuándo elegirías Foundry Agent Service en un banco?* Cuando el gobierno (red privada, RBAC, Content Safety, auditoría) pesa más que el control fino.

## 7. Cómo lo demuestro
Diagrama del grafo (`graph.get_graph().draw_mermaid()`) + demo de un flujo que pasa por el agente Foundry con citas y vuelve al grafo.

## 8. Recursos
- https://langchain-ai.github.io/langgraph/concepts/low_level/
- https://langchain-ai.github.io/langgraph/concepts/multi_agent/
- https://learn.microsoft.com/azure/ai-foundry/agents/overview
- https://learn.microsoft.com/azure/ai-foundry/how-to/develop/agent-evaluate-sdk
