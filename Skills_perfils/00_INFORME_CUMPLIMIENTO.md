# Informe de cumplimiento — WebGIS + Chat IA vs. "Software Engineer Backend (GenAI) – Interbank"

Auditoría hecha sobre el código real del repositorio `gis-backend-app` (rama `developer`, commit `78b9fe8`) y una pasada ligera al frontend `Fronted_IA_Python`. Fecha: 2026-10-09.

## Resumen ejecutivo

El proyecto es **un backend GenAI funcional y bien razonado, pero con perfil de "producto de una instancia"**, no de "servicio bancario de alta disponibilidad". Lo más fuerte es el diseño del puerto `ILLMProvider` con cuatro adaptadores (incluido un **agente de Microsoft Foundry consumido vía Responses API** con traducción bidireccional y tests), la validación Pydantic de parámetros de las skills contra inyección SQL, y el módulo de **tracking en tiempo real** (WebSocket + Redis pub/sub + cortocircuito + máquina de estados de visitas), que es lo más "producción" del repo.

Las brechas que más pesan para el puesto:

1. **El camino del LLM no es asíncrono**: todo el grafo LangGraph corre síncrono dentro de `asyncio.to_thread`, con timeouts implementados con hilos daemon que quedan vivos tras expirar. No hay `AsyncAzureOpenAI`, ni `ainvoke`, ni streaming.
2. **Tool calling de un solo paso**: se ejecuta solo `tool_calls[0]` y no hay bucle agente → herramienta → agente.
3. **Memoria LRU en proceso**: se pierde al reiniciar y no se comparte entre réplicas (con `max-replicas 3` el usuario pierde contexto según a qué réplica caiga).
4. **Observabilidad casi nula**: `print`/`logging` en texto plano, sin `trace_id`, sin OpenTelemetry, sin métricas de tokens ni latencia por herramienta.
5. **CI/CD incompleto**: solo pylint corre en CI; los tests no corren en el pipeline; el workflow de despliegue a Azure está **inactivo** y el despliegue real es **Seenode**, no Azure Container Apps.

Sin RAG, sin rate limiting, sin auditoría de interacciones LLM, sin protección explícita contra prompt injection ni filtrado de PII. Con 5–6 semanas de trabajo enfocado (ver `01_ROADMAP.md`), el proyecto pasa de "demo sólida" a "caso de estudio defendible en una entrevista de banca".

**Nivel global estimado: ~55% del perfil cubierto con evidencia de código.** Indispensables: 2 ✅, 3 🟡, 1 🔍. Deseables: 1 🟡, 1 ❌.

## Matriz de cumplimiento

| ID | Requisito | Estado | Evidencia | Qué falta | Riesgo en banca | Esfuerzo |
|---|---|---|---|---|---|---|
| F1 | Servicios async que orquestan agentes | 🟡 | `src/presentation/api/orchestrator.py:150` (`asyncio.to_thread`), `src/application/orchestration/langgraph_flow.py` (`StateGraph` + `add_conditional_edges`) | Grafo síncrono (`graph.invoke`, línea ~97); agentes no async; sin streaming; sin extracción/validación con LLM (el `ValidatorAgent` solo verifica que el texto no esté vacío) | Alto: cada consulta ocupa un hilo del pool por toda la latencia del LLM (hasta 2 llamadas) | M |
| F2 | LLMs en prod: tool calling, contexto, memoria, observabilidad | 🟡 | `src/domain/ports/illm_provider.py`, `src/infrastructure/llm/base.py`, `src/infrastructure/llm/azure_agent.py`, `src/application/agents/agenteConsultaGIS.py:106,199`, `src/application/services/conversation_memory.py` | Bucle multi-herramienta; tool calls paralelos; memoria persistente; conteo de tokens; trazas | Alto | M-L |
| F3 | Lógica de negocio end-to-end y estados complejos | 🟡 | `src/domain/entities/visita_validacion.py:20` (`EstadoVisita` + transiciones con guardas), comandos CQRS en `src/application/commands/` | Estado del flujo IA no persistido (sin checkpointer); sin idempotency keys en endpoints; sin outbox/compensación; sin human-in-the-loop | Medio | M |
| F4 | Despliegue y operación en nube (CI/CD, contenedores, observabilidad) | 🟡 | `docker/Dockerfile` (multi-stage, usuario no root, HEALTHCHECK), `.github/workflows/ci-cd-deploy.yaml` (OIDC, ACR, health check post-deploy), `infra/provision-container-apps.ps1:142-144` | Workflow de deploy **inactivo** (`workflow_dispatch`); destino real Seenode; tests no corren en CI; sin escaneo de imagen; sin IaC declarativo (Bicep/Terraform); sin observabilidad | Alto | M |
| F5 | Latencia y escalabilidad en tiempo real | 🟡 | `src/presentation/ws/connection_manager.py` (WS + puente pub/sub entre réplicas), `src/infrastructure/redis_tracking/connection.py:87` (`_Circuito`), pool SQLAlchemy en `src/infrastructure/database/connection.py:26-33` | Tracking sí escala; el chat no (sin streaming, memoria local, `to_thread` acotado por el pool de hilos por defecto); `/ready` bloquea el loop (`health.py:22`) | Medio-alto | M |
| R1 | 5 años backend alta disponibilidad | 🔍 | No verificable por código | Narrativa: usar el proyecto como evidencia de decisiones (ver más abajo) | — | — |
| R2 | Servicios async y APIs en prod con foco en latencia | 🟡 | Tracking async real (`actualizar_ubicacion_command.py:115,174`, `redis.asyncio`); chat síncrono envuelto | Medir p95, streaming SSE, async end-to-end en el path LLM, pruebas de carga | Alto | M |
| R3 | Python avanzado + asyncio | 🟡 | 112 `async def`; `asyncio.gather`, `asyncio.to_thread`, `redis.asyncio`; dataclasses, ABC, Pydantic v2, `Enum` | Sin `TaskGroup`, `Semaphore`, cancelación cooperativa; timeouts con `threading.Thread` en vez de `asyncio.timeout` (`base.py:184`) | Medio | S-M |
| R4 | FastAPI y REST | ✅ | `app.py` (factory, routers con `Depends(get_current_user)`, CORS, handler global), `src/presentation/schemas.py`, versionado `/api/v1`, `docs/swagger.yaml` | Esquema de error inconsistente entre routers; `on_event` deprecado (`app.py:282`); `/api/ai/*` sin versión; `message` sin `max_length` (`schemas.py:152`) | Bajo | S |
| R5 | LLMs en prod + integración de terceros | 🟡 | 4 adaptadores LLM (Strategy + Factory en `src/infrastructure/llm/__init__.py`), Foundry Agent con Entra ID (`azure_agent.py`, `entra.py`), GeoServer/Martin/Redis como terceros | Sin retries/backoff ni fallback entre proveedores; un solo tool call; sin métricas de tokens/costo | Alto | M |
| R6 | Cloud, Docker, CI/CD | 🟡 | Dockerfile, compose, OIDC GitHub→Azure (`infra/setup-github-oidc.ps1`), Managed Identity system-assigned | Pipeline con tests + escaneo + entornos + rollback; deploy Azure reactivado o justificado | Medio | M |
| D1 | Orquestación agéntica (LangGraph/LangChain) | 🟡 | `langgraph==1.0.9`, `StateGraph`, `add_conditional_edges` en `langgraph_flow.py`; Foundry Agent Service en `azure_agent.py` + `scripts/sync_agent_tools.py` | Router por regex, no por LLM; sin checkpointer, `interrupt`, `Command`, subgrafos ni ciclos | Medio | M |
| D2 | RAG y bases vectoriales | ❌ | Ninguna (0 coincidencias de `embedding`, `pgvector`). El adaptador Foundry admite `file_search`/`azure_ai_search` gestionados **si se configuran en el portal** (🔍) | Todo | Bajo (deseable) | M |

## Top 5 fortalezas para vender en entrevista

1. **Puerto `ILLMProvider` + 4 adaptadores** (`src/domain/ports/illm_provider.py`, `src/infrastructure/llm/`): cambio de proveedor por variable de entorno, `MisconfiguredProvider` como Null Object para que la API levante aunque el LLM falle, y formato canónico chat.completions.
2. **Integración con Foundry Agent Service** (`src/infrastructure/llm/azure_agent.py`): traducción chat.completions ↔ Responses API, manejo del endpoint "sellado" (`tools`/`instructions` rechazados por petición), sincronización de tools desde código (`scripts/sync_agent_tools.py`), Entra ID sin secretos y detección de entorno para saltar IMDS en local (`entra.py`). Cubierto por tests (`tests/unit/test_azure_agent.py`).
3. **Tool calling seguro**: parámetros de cada skill validados con Pydantic y whitelist de identificadores SQL (`src/application/skills/skill_params.py`), registro central con errores normalizados (`skills/registry.py`).
4. **Tiempo real que escala horizontalmente**: WebSocket + Redis pub/sub entre réplicas + cortocircuito + modo degradado sin Redis (`src/presentation/ws/`, `src/infrastructure/redis_tracking/`), con máquina de estados de dominio (`visita_validacion.py`).
5. **Higiene de seguridad pensada**: JWT validado al arrancar (`app.py`), redacción de campos sensibles en logs (`_redactar_body`), session_id namespaced por usuario para evitar lectura cruzada de historiales (`orchestrator.py`), migración de `python-jose`→`PyJWT` y `passlib`→`bcrypt` por CVEs (`requirements.txt`).

## Top 5 brechas críticas

1. **Path LLM síncrono + timeouts con hilos huérfanos** (`base.py:184-210`): tras un timeout el hilo sigue consumiendo la llamada HTTP. Bajo carga esto agota el pool de `to_thread` (default `min(32, cpu+4)`).
2. **Sin observabilidad de LLM**: no hay `trace_id`, tokens, costo ni latencia por nodo/herramienta. En banca es lo primero que preguntan.
3. **Memoria conversacional no distribuida** (`conversation_memory.py`): incompatible con `max-replicas 3` y con reinicios.
4. **Tool calling de un paso** (`agenteConsultaGIS.py:199`): descarta tool calls adicionales y no permite encadenar (p. ej. filtrar y luego buffer).
5. **Pipeline sin tests ni escaneo, deploy Azure inactivo** (`.github/workflows/`): no se puede afirmar "CI/CD en Azure" hoy.

## Discrepancias README/supuestos vs. código

| Afirmación | Realidad en el código |
|---|---|
| "Despliegue en Azure Container Apps con Managed Identity y ACR" | El workflow está marcado **INACTIVO** (`ci-cd-deploy.yaml`, cabecera) y el README dice que el destino activo es **Seenode** (`README.md:164`). Container Apps existe solo como script (`infra/provision-container-apps.ps1`). |
| Grafo "IntentRouter → MapContextAgent / GISAgent → Validator" | Correcto, pero el IntentRouter es **regex** (`agents_wrappers.py:27`), no un LLM, y el Validator solo verifica respuesta no vacía. |
| "Acción directa del frontend (bypass del LLM)" | `orchestrator.py:129` escribe `context["direct_action"]` pero **nadie lo lee** en `src/` → código muerto. |
| Memoria "LRU por sesión" | Correcto, pero solo en proceso (`OrderedDict` + `threading.Lock`). |
| README `pytest tests/unit -q` | Funciona (94 tests pasan con `venv`), pero CI **no** lo ejecuta y `pytest` no está en el Python global. |
| Singleton del orquestador | `langgraph_flow.py:151-152` instancia `LangGraphOrchestrator()` **dos veces** (duplica construcción de agentes al importar). |

## Resultado de herramientas

- `venv/Scripts/python -m pytest tests/unit -q` → **94 passed in 1.98s**.
- Linter: no se ejecutó pylint localmente; CI exige `--fail-under=9.5`.
- No hay tests de `orchestrator`, `langgraph_flow`, `agenteConsultaGIS` ni de skills.

## R1 (años de experiencia) como narrativa

El código no prueba años, pero sí **criterio**. Prepara 3 historias STAR con este repo:
- *Cold start de Entra ID*: problema (primer request 400/timeout), decisión (conexión perezosa adoptable + warm-up + saltar IMDS fuera de Azure), resultado.
- *Escalar WebSocket a varias réplicas*: Redis pub/sub + cortocircuito + modo degradado.
- *Agente Foundry sellado*: descubrir el 400 "Not allowed when agent is specified" y diseñar `sync_agent_tools.py`.
