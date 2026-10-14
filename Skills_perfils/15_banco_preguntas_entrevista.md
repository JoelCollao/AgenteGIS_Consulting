# Banco de preguntas de entrevista

Formato: **Pregunta** → respuesta guía → *cómo lo ilustro con mi proyecto*.

## Python y asyncio
1. **¿Qué bloquea el event loop y cómo lo detectas?** Llamadas síncronas de I/O o CPU dentro de `async def`; se detecta con `PYTHONASYNCIODEBUG` / slow callbacks y latencia de endpoints triviales. → *`/ready` hace `engine.connect()` síncrono (`health.py:22`); lo corregiría con `to_thread`.*
2. **`gather` vs `TaskGroup`.** TaskGroup cancela hermanas si una falla y agrupa excepciones (`ExceptionGroup`). → *uso `gather(return_exceptions=True)` en `actualizar_ubicacion_command.py:174` para drenar tareas sin cortar.*
3. **¿Cómo pones un timeout que realmente cancele?** `asyncio.timeout`; un hilo no se cancela. → *mi `base.py:184` usa hilos daemon: explico el trade-off y la migración a `AsyncAzureOpenAI`.*
4. **¿Cuándo usas hilos, procesos o async?** I/O con librerías async → async; I/O con librerías sync → hilos; CPU → procesos. → *geoprocesos shapely pesados irían a ProcessPool o a un worker.*
5. **¿Cómo limitas concurrencia hacia un servicio externo?** `asyncio.Semaphore` local + rate limiter distribuido.

## FastAPI / REST
6. **¿Cómo proteges todas las rutas salvo algunas?** Dependencias a nivel de router. → *`app.py`, lista `_routers` con `_auth_required`.*
7. **¿Cómo diseñas errores?** RFC 9457 con `trace_id`. → *hoy tengo 3 formatos; lo unificaría.*
8. **Idempotencia en POST.** `Idempotency-Key` + almacenamiento de respuesta + constraint único.
9. **¿Por qué `lifespan` y no `on_event`?** Recursos con ciclo de vida claro (`yield`), `on_event` deprecado. → *`app.py:282`.*
10. **Liveness vs readiness.** → *`/health` y `/ready` en `health.py`.*

## LLM en producción y tool calling
11. **Explica el ciclo de function calling.** El modelo devuelve `tool_calls`; ejecuto, devuelvo `role=tool` con `tool_call_id`; repito hasta respuesta final. → *`agenteConsultaGIS.py` (y su limitación: un paso, solo `tool_calls[0]`).*
12. **¿Cómo validas los argumentos que propone el modelo?** Pydantic + whitelist; nunca SQL libre. → *`skill_params.py`.*
13. **¿Cómo evitas loops infinitos de herramientas?** Máximo de pasos, presupuesto de tokens, detección de llamadas repetidas.
14. **¿Cómo abstraes el proveedor de LLM?** Puerto + adaptadores + factory + Null Object. → *`ILLMProvider`, `build_llm_provider`, `MisconfiguredProvider`.*
15. **Chat Completions vs Responses API.** Responses unifica tools gestionadas y estado; formato de items `function_call`/`function_call_output`. → *traducción en `azure_agent.py`.*
16. **¿Cómo manejas el contexto para no pasarte de tokens?** Presupuesto con tiktoken, resumen, resultados de tools resumidos. → *`_summarize_result` no manda GeoJSON completo.*
17. **¿Por qué tu memoria LRU no sirve en producción?** No persiste, no se comparte entre réplicas, no es auditable. Migración a checkpointer Postgres.
18. **¿Qué es prompt caching y cómo lo aprovechas?** Prefijo estable (system + tools) al inicio; reduce latencia y costo.

## Observabilidad
19. **Un cliente se queja de una respuesta de ayer. ¿Cómo la investigas?** Trazas por `trace_id` con prompt, tool, args, tokens. → *hoy solo logs; plan O1–O4.*
20. **¿Qué métricas pondrías en un dashboard GenAI?** p50/p95 latencia y TTFT, error rate por código, 429, tokens y costo por día/usuario, tool-accuracy, fallback rate.
21. **¿Qué atributos lleva un span de LLM?** Semconv GenAI: modelo, tokens in/out, finish reason, operación.

## Resiliencia y terceros
22. **Azure OpenAI responde 429. ¿Qué haces?** Retry con `Retry-After` y jitter, limiter propio, múltiples deployments/APIM, caché, degradación.
23. **Circuit breaker: explica estados.** → *`_Circuito` en `redis_tracking/connection.py:87`.*
24. **Fallback entre modelos sin romper calidad.** Modelo secundario con eval previa; marcar la respuesta como degradada.
25. **Cold start con Managed Identity.** → *conexión perezosa adoptable + warm-up + saltar IMDS fuera de Azure (`base.py`, `entra.py`).*

## Orquestación agéntica
26. **¿Qué aporta LangGraph frente a un bucle propio?** Estado tipado, ciclos, checkpointing, HITL, streaming de eventos. → *mi grafo hoy es un DAG; con ciclo y checkpointer aprovecharía el framework.*
27. **LangGraph vs Foundry Agent Service.** Control vs gestionado (ver archivo 11).
28. **¿Cómo haces human-in-the-loop?** `interrupt()` + checkpointer + endpoint de resume.
29. **Router con regex vs LLM.** Regex: barato y determinista para intents triviales; LLM para el resto. → *`IntentRouterAgent`.*

## Estado y negocio
30. **Modela una solicitud con estados.** Enum + transiciones con guardas en el dominio. → *`EstadoVisita` en `visita_validacion.py`.*
31. **Outbox: ¿qué problema resuelve?** Atomicidad entre BD y publicación de eventos.
32. **¿Dónde va la lógica de negocio cuando hay agentes?** En el dominio, determinista; el LLM extrae y propone.

## Cloud / CI/CD / seguridad
33. **Describe tu pipeline ideal.** Lint → tests → evals rápidas → build → escaneo → deploy dev → aprobación → prod por revisión → smoke → rollback. → *OIDC ya hecho en `ci-cd-deploy.yaml`.*
34. **¿Cómo autentica tu app contra Azure OpenAI sin secretos?** Managed Identity + RBAC.
35. **Prompt injection directa e indirecta: defensas.** Prompt Shields, delimitar datos, mínimo privilegio en tools, validación, HITL.
36. **PII en logs y memoria.** Enmascarado antes de persistir; retención; acceso restringido.
37. **RAG: ¿cómo lo evalúas?** recall@k/MRR + groundedness/relevance; dataset dorado.

## Diseño de sistemas

### D-1. Asistente de onboarding bancario con agentes que extraen datos de documentos y validan reglas
- **Requisitos**: subir DNI/recibo/boletas, extraer campos, validar reglas (edad, ingresos, listas de sanciones), pedir faltantes, decidir o escalar a analista. Auditoría completa, PII protegida, latencia < 3 s para interacción, procesos que duran días.
- **Arquitectura**: FastAPI async (SSE) → LangGraph con checkpointer Postgres (`thread_id = solicitud`) → nodos: `IntakeAgent` (conversación), `ExtractionAgent` (Azure Document Intelligence + LLM con structured output Pydantic), `RulesEngine` determinista (no LLM), `KYCIntegration` (terceros con timeout/retry/circuit breaker), `interrupt()` para analista humano, `NotifierAgent`.
- **Estado**: `SolicitudOnboarding` con máquina de estados (`INICIADA → DOCS_PENDIENTES → EN_VALIDACION → EN_REVISION_HUMANA → APROBADA/RECHAZADA`), idempotency keys en cada comando, outbox para eventos (Service Bus).
- **Asíncrono**: extracción y KYC como jobs en cola; el chat consulta estado y recibe eventos.
- **Seguridad**: Entra ID, Prompt Shields, PII enmascarada en trazas, documentos en Blob con CMK, Private Endpoints, `ai_audit`.
- **Observabilidad**: OTel → App Insights; métricas de tiempo por estado, tasa de escalamiento humano, exactitud de extracción.
- *Mi proyecto*: misma forma que `EstadoVisita` + skills validadas + puerto LLM intercambiable.

### D-2. Chat de atención al cliente para 10 000 usuarios concurrentes
- Estimar: si latencia media 6 s y 1 000 msg/s → 6 000 conversaciones en vuelo (Little). Stateless + Redis/Postgres para estado; SSE; semáforos y rate limit por usuario; varios deployments/regiones de Azure OpenAI detrás de APIM con balanceo y `token-limit`; caché de FAQs; router barato antes del LLM grande; KEDA por concurrencia; degradación a respuestas predefinidas.
- *Mi proyecto*: WebSocket/pub-sub multi-réplica del tracking; router regex como fast-path.

### D-3. Agente que ejecuta operaciones (p. ej. bloqueo de tarjeta) de forma segura
- Tools con mínimo privilegio y scopes por usuario; confirmación explícita (HITL o paso de confirmación del cliente con OTP); idempotencia; validación determinista previa; auditoría inmutable; límites (máx. N acciones por sesión); pruebas de red teaming.
- *Mi proyecto*: validación Pydantic de tools + propuesta de `interrupt()` antes de skills con efecto.
