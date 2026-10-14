# Observabilidad y debugging de LLM
**Requisito(s) del perfil:** R5, F2 (observabilidad y debugging de LLMs), F4 (operación en la nube).
**Estado en mi proyecto:** ❌ (solo logs de texto)

## 1. Qué pide el perfil, en concreto
Poder responder en minutos: ¿qué le llegó al modelo?, ¿qué tool eligió y con qué args?, ¿cuánto tardó cada paso?, ¿cuántos tokens y cuánto costó?, ¿qué falló y en qué réplica? Correlacionar todo con un `trace_id`.

## 2. Qué tengo hoy (evidencia)
- `app.py` → `logging.basicConfig` en texto plano + `_log()` con `sys.stdout.write`; middleware que imprime método/ruta/duración; redacción de secretos (`_redactar_body`). Bueno para dev, insuficiente para prod.
- `langgraph_flow.py` → `processing_time_ms` en la respuesta.
- Logs por nodo (`[IntentRouter]`, `[GISAgent]`, `[Validator]`) en `agents_wrappers.py`.
- `health.py` → `/api/v1/health` (liveness) y `/api/v1/ready` (readiness con DB).
- 0 coincidencias de `opentelemetry`, `trace_id`, `correlation`, `request_id`, `applicationinsights`, `structlog`.

## 3. Brechas
Sin correlación, sin métricas, sin trazas distribuidas, sin tokens/costo, sin tracing de Foundry, sin alertas, sin dashboards.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| O1 | Logging JSON (`python-json-logger` o `structlog`) con `request_id`, `user`, `session`, `node`, `tool` vía `contextvars` | `src/config/logging.py`, `app.py` | Logs parseables en Log Analytics | S |
| O2 | `configure_azure_monitor()` (`azure-monitor-opentelemetry`) + instrumentación FastAPI, SQLAlchemy, Redis, httpx | `src/config/telemetry.py` | Transacción end-to-end en App Insights | S |
| O3 | Spans manuales por nodo LangGraph y por tool con atributos GenAI semconv (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`) | `agents_wrappers.py`, `base.py` | Span tree visible | M |
| O4 | Tracing de Foundry: `AIProjectClient.telemetry.get_application_insights_connection_string()` + `OpenAIInstrumentor` | `azure_foundry.py`, `azure_agent.py` | Trazas en la pestaña Tracing del proyecto Foundry | S |
| O5 | Métricas: histogramas `llm_latency_ms`, `tool_latency_ms`, contador `llm_tokens_total{model,type}`, `llm_errors_total{code}`, costo estimado | `telemetry.py` | Dashboard (Workbook) con p95 y costo/día | M |
| O6 | Alertas: error rate > 5%, p95 > 8 s, 429 sostenidos | Bicep | Alerta de prueba disparada | S |
| O7 | Política de contenido en trazas: prompts completos solo en entornos no prod o con PII enmascarada | `telemetry.py` | Revisión manual | S |

## 5. Teoría que debo dominar para la entrevista
- Tres señales: logs, métricas, trazas; trazas unen todo con `trace_id` (W3C `traceparent`).
- Semantic conventions de GenAI en OpenTelemetry.
- Debugging LLM: reproducir con el mismo prompt + seed/temperature 0, comparar versiones de prompt, revisar tool args.
- p95/p99 > promedio; latencia por componente (cola, LLM, tool, DB).
- Costo = tokens_in × precio_in + tokens_out × precio_out; atribuir por usuario/feature.
- Privacidad: en banca los prompts pueden contener PII → muestreo, enmascarado, retención corta.

## 6. Preguntas probables y respuesta guía
- *Un usuario dice que el bot respondió mal ayer, ¿qué haces?* Busco por `user`/`session` → `trace_id` → veo prompt, tool elegida, args, resultado, tokens. Hoy no podría hacerlo con trazas: lo reconozco y explico O1–O4.
- *¿Cómo detectas regresiones al cambiar de prompt?* Evals offline + métricas de tool accuracy y feedback en producción.

## 7. Cómo lo demuestro
Captura del end-to-end transaction de App Insights con spans `POST /orchestrator → intent_router → gis_agent → llm.chat → tool.buffer_analysis → llm.chat`, y Workbook de tokens/costo.

## 8. Recursos
- https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-enable?tabs=python
- https://opentelemetry.io/docs/specs/semconv/gen-ai/
- https://learn.microsoft.com/azure/ai-foundry/how-to/develop/trace-application
