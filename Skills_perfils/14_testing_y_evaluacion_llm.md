# Testing y evaluación de LLM
**Requisito(s) del perfil:** Transversal (calidad para R5/F2 y CI/CD de R6).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Pruebas deterministas del código alrededor del LLM (con LLM simulado), pruebas de integración contra dependencias reales, y **evaluación** de calidad del comportamiento del modelo (selección de tools, groundedness) que corra en CI.

## 2. Qué tengo hoy (evidencia)
- `tests/unit/` → 5 archivos, **94 tests pasan** (`venv/Scripts/python -m pytest tests/unit -q`, 1.98 s).
  - `test_azure_agent.py` → traducción chat.completions ↔ Responses API con dobles (`_Obj`), sin red. Buen ejemplo de mock de SDK.
  - `test_tracking_dominio.py`, `test_tracking_aplicacion.py`, `test_tracking_redis_repositorio.py`, `test_redis_conexion.py` → dominio y aplicación del tracking.
- `tests/integration/` → vacío.
- Front: `jest` + `@testing-library/react` (`Fronted_IA_Python/package.json`, `tests/`).
- CI no ejecuta tests (`ci-cd-lint.yaml`). Sin `pytest-asyncio` declarado, sin `respx`, sin cobertura, sin mypy/ruff.

## 3. Brechas
- No hay tests de `AgenteConsultaGIS`, del grafo, del orquestador, de skills ni de endpoints (`TestClient`).
- Sin evaluación del LLM ni datasets dorados.
- Sin tests de integración con PostGIS (testcontainers).

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| TE1 | `FakeLLMProvider(ILLMProvider)` con respuestas guionadas | `tests/fakes/llm.py` | Reutilizable | S |
| TE2 | Tests del agente: conversacional, tool válida, tool inexistente, JSON inválido, skill que falla, timeout, narración que falla | `tests/unit/test_agente_gis.py` | 7 casos verdes | S |
| TE3 | Tests del grafo: routing `map_context` vs `gis_agent`, validator | `tests/unit/test_langgraph_flow.py` | Verde | S |
| TE4 | Tests de API con `httpx.AsyncClient(transport=ASGITransport(app))` + override de `get_current_user` | `tests/api/` | 401 sin token, 200/422 | S |
| TE5 | `respx` para 429/500/timeout del SDK OpenAI | `tests/unit/test_llm_resilience.py` | Verde | S |
| TE6 | Integración PostGIS con `testcontainers[postgres]` (imagen `postgis/postgis`) para skills | `tests/integration/` | Job separado en CI | M |
| TE7 | Evals: `tests/evals/tool_selection.jsonl` (≥ 40 casos) → accuracy de tool + args; `azure-ai-evaluation` (`ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator`, `GroundednessEvaluator`) | `tests/evals/run_evals.py` | Job nocturno/manual con umbrales | M |
| TE8 | `ruff` + `mypy --strict` gradual en `src/domain` y `src/application` | `pyproject.toml`, CI | Sin errores en esos paquetes | S |

## 5. Teoría que debo dominar para la entrevista
- Pirámide: muchos unitarios con LLM falso (rápidos, deterministas), pocos de integración, evals aparte (no deterministas, con umbrales y tendencias).
- Qué testear sin LLM: construcción de prompts, parsing de tool calls, validación, manejo de errores, límites de tokens.
- Evals: dataset dorado versionado; métricas exactas (tool correcta, args) + LLM-as-judge (groundedness, relevancia) calibrado con muestras humanas; detectar regresiones al cambiar prompt o modelo.
- Snapshot/contract tests de OpenAPI.
- Red teaming automatizado (Azure AI Red Teaming Agent / PyRIT).

## 6. Preguntas probables y respuesta guía
- *¿Cómo testeas algo no determinista?* Separo lo determinista (todo el código alrededor) con fakes; lo no determinista se evalúa con datasets y umbrales, temperatura baja y varias corridas.
- *¿Cómo decides si un nuevo modelo es mejor?* Mismo dataset, métricas de calidad + latencia + costo; A/B o shadow en producción.

## 7. Cómo lo demuestro
`pytest -q` en vivo + reporte de evals (tabla accuracy por tool) + cobertura en el PR.

## 8. Recursos
- https://docs.pytest.org/
- https://lundberg.github.io/respx/
- https://learn.microsoft.com/azure/ai-foundry/how-to/develop/evaluate-sdk
- https://testcontainers-python.readthedocs.io/
