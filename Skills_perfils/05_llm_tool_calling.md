# LLM tool calling
**Requisito(s) del perfil:** R5 (LLMs en producción: tool/function calling), F2 (integrar LLMs en producción).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Definir herramientas con esquemas sólidos, validar argumentos, ejecutar el bucle agente↔herramientas hasta resolver, soportar llamadas paralelas y manejar errores de herramientas sin romper la conversación.

## 2. Qué tengo hoy (evidencia)
- `src/application/agents/system_prompts.py:67` → `GIS_TOOL_DEFINITIONS` (JSON Schema, 8 skills).
- `src/application/agents/agenteConsultaGIS.py:106` → `llm.chat(messages, tools=GIS_TOOL_DEFINITIONS)`.
- `agenteConsultaGIS.py:199` → `tool_call = llm_response.tool_calls[0]` → **solo la primera**.
- `agenteConsultaGIS.py:203-206` → si los argumentos no son JSON, usa `{}` en silencio.
- `src/application/skills/skill_params.py` → Pydantic con whitelist de identificadores SQL (`_SQL_ID_PATTERN`) y rangos (`distance ≤ 100000`, `limit ≤ 2000`). Muy bueno.
- `src/application/skills/registry.py` → errores de skill normalizados `{"success": False, "error": ...}`.
- `_narrate_result` → segunda llamada sin tools para redactar (patrón correcto, reenvía `reasoning_details`).
- `src/infrastructure/llm/azure_agent.py` → traducción `function_call`/`function_call_output` de la Responses API y `tool_choice="none"` en la narración; tools publicadas al agente con `scripts/sync_agent_tools.py`.
- `tests/unit/test_azure_agent.py` → cubre la traducción.

## 3. Brechas
- No hay bucle: el modelo no puede pedir otra herramienta tras ver el resultado.
- Tool calls paralelos descartados.
- Argumentos inválidos no se devuelven al modelo para autocorrección.
- El esquema de las tools y los modelos Pydantic están duplicados a mano (riesgo de deriva).
- Sin límites de iteraciones/costo, sin métricas de tool-selection accuracy.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| T1 | Bucle `while response.has_tool_calls and step < MAX_STEPS (5)`; añade mensaje assistant con todos los `tool_calls` y un mensaje `tool` por cada uno | `agenteConsultaGIS.py` | Test con FakeLLM que pide 2 tools en 2 turnos | M |
| T2 | Ejecución paralela de tool calls independientes (`TaskGroup` + `Semaphore`) | idem | Test de tiempos | S |
| T3 | Error de validación → mensaje `tool` con `{"error": "...", "hint": "..."}` para que el modelo corrija (máx. 1 reintento) | idem, `registry.py` | Test: args inválidos → 2.º intento válido | S |
| T4 | Generar `GIS_TOOL_DEFINITIONS` desde los modelos Pydantic (`model_json_schema()`) | `system_prompts.py`, `skill_params.py` | Un solo origen de verdad; test que compara | S |
| T5 | Guardas: allowlist de tools por rol de usuario, presupuesto de tokens por request | `agenteConsultaGIS.py` | Usuario sin permiso no ejecuta skill | S |
| T6 | Dataset de 40 frases → tool esperada + args; métrica accuracy en CI | `tests/evals/tool_selection.jsonl` | ≥ 90% | M |

## 5. Teoría que debo dominar para la entrevista
- Ciclo: el modelo **no ejecuta** nada; devuelve intención estructurada; mi código valida, ejecuta y devuelve `role=tool` con el `tool_call_id`.
- `tool_choice`: `auto`, `none`, `required`, o forzar una función.
- **Structured outputs / strict**: con `strict: true` todos los campos deben ser `required` (por eso pongo `strict: False` en `azure_agent.py`).
- Parallel tool calls: útil para lecturas independientes; peligroso para acciones con efectos.
- Herramientas = superficie de ataque: validar como si fueran input de usuario (lo hago con Pydantic).
- Errores típicos: no devolver resultado de cada `tool_call_id` (400 del API), bucles infinitos, enviar resultados gigantes (resumo el GeoJSON en `_summarize_result`).

## 6. Preguntas probables y respuesta guía
- *¿Cómo evitas que el LLM haga SQL injection a través de una tool?* Nunca SQL libre: parámetros tipados, whitelist de identificadores, queries parametrizadas.
- *¿Qué haces si el modelo alucina una tool?* El registro devuelve error con lista de válidas; se devuelve al modelo.
- *¿Cómo funciona en Foundry Agent Service?* Las funciones viven en la definición del agente; el cliente ejecuta las funciones locales y devuelve `function_call_output`; las tools gestionadas (file_search, bing) las ejecuta el servicio.

## 7. Cómo lo demuestro
Traza (App Insights o logs) de "filtra los colegios del distrito X y hazles buffer de 300 m" con dos spans de tool, más el reporte de tool-selection accuracy.

## 8. Recursos
- https://platform.openai.com/docs/guides/function-calling
- https://learn.microsoft.com/azure/ai-foundry/openai/how-to/function-calling
- https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/function-calling
