# Integración con terceros y resiliencia
**Requisito(s) del perfil:** R5 (integración de servicios de terceros), F5 (integraciones críticas en tiempo real).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Que una dependencia lenta o caída (LLM, core bancario, bureau) no tumbe el servicio: timeouts, reintentos con backoff, circuit breaker, fallback, respeto de rate limits y degradación controlada.

## 2. Qué tengo hoy (evidencia)
- `src/infrastructure/llm/base.py:184` → timeout duro por llamada LLM (hilo daemon). `_ensure_client` con conexión perezosa "adoptable" para cold start de Entra ID.
- `src/infrastructure/llm/misconfigured.py` → Null Object: la API levanta aunque el LLM esté mal configurado.
- `agenteConsultaGIS.py` → `_fallback_response` si el LLM falla.
- `src/infrastructure/redis_tracking/connection.py:87` → **circuit breaker** propio (`_Circuito`) + reconexión; `pubsub.py` con backoff. Modo degradado sin Redis.
- `src/infrastructure/database/connection.py` → `pool_pre_ping`, `pool_timeout`, `connect_timeout`.
- `geoserver/client.py`, `martin/client.py` → `requests` síncrono.

## 3. Brechas
- Sin retries sobre 429/5xx del LLM y sin leer `Retry-After`.
- Sin fallback entre deployments/regiones/proveedores, aunque la Factory ya lo permitiría.
- Sin circuit breaker para el LLM.
- Sin rate limit propio (protege cuota TPM/RPM de Azure OpenAI).
- El hilo tras timeout sigue consumiendo cuota.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| RS1 | `tenacity`: `retry_if_exception_type((RateLimitError, APIConnectionError, InternalServerError))`, `wait_random_exponential(1, 20)`, `stop_after_attempt(3)`, respetar `Retry-After`; `max_retries=0` en el SDK para no duplicar | `base.py` | Test `respx`: 429→429→200 OK | S |
| RS2 | `FallbackLLMProvider` (Composite del puerto): primario `gpt-4.1` → secundario `gpt-4.1-mini` u otra región | `src/infrastructure/llm/fallback.py`, `llm_settings.py` | Test: primario 500×3 → responde secundario; métrica `llm_fallback_total` | S |
| RS3 | Circuit breaker para LLM reutilizando la lógica de `_Circuito` (extraerla a `src/infrastructure/resilience/`) | idem | Tras N fallos, falla rápido durante T s | S |
| RS4 | Rate limiting por usuario y global (token bucket en Redis, estimando tokens) | middleware | 429 propio antes de agotar la cuota de Azure | M |
| RS5 | `httpx.AsyncClient` con timeouts por fase (connect/read) para GeoServer/Martin | clientes | Sin `requests` en camino async | M |
| RS6 | Bulkhead: semáforos separados para LLM, PostGIS pesado y GeoServer | — | Saturar GeoServer no afecta al chat | S |

## 5. Teoría que debo dominar para la entrevista
- Timeout siempre (connect + read + total); deadline propagado desde la request.
- Retry solo en errores transitorios e idempotentes; backoff exponencial con jitter; presupuesto de reintentos para no amplificar caídas (retry storm).
- Circuit breaker: closed/open/half-open.
- Rate limits de Azure OpenAI: TPM y RPM por deployment; 429 con `retry-after-ms`; PTU vs pay-as-you-go; APIM como gateway con balanceo entre backends y `azure-openai-token-limit`.
- Fallback con degradación explícita (decirle al usuario que la respuesta es limitada).
- Bulkhead para aislar dependencias.

## 6. Preguntas probables y respuesta guía
- *Azure OpenAI devuelve 429 en horas pico, ¿qué haces?* Retry con `Retry-After`, rate limiter propio, varios deployments/regiones detrás de APIM, caché, priorizar tráfico, y a largo plazo PTU.
- *¿Diferencia entre timeout del SDK y el tuyo?* El del SDK puede no cubrir todo (lo viví: por eso el timeout duro); en async uso `asyncio.timeout` que sí cancela.

## 7. Cómo lo demuestro
Tests con `respx` de 429/500/timeout + demo con el primario apagado respondiendo por fallback (header `X-LLM-Fallback: true`).

## 8. Recursos
- https://tenacity.readthedocs.io/
- https://learn.microsoft.com/azure/ai-foundry/openai/quotas-limits
- https://learn.microsoft.com/azure/api-management/azure-openai-token-limit-policy
- https://learn.microsoft.com/azure/architecture/patterns/circuit-breaker
