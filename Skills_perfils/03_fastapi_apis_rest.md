# FastAPI y APIs REST
**Requisito(s) del perfil:** R4 — FastAPI (o equivalente) y APIs REST.
**Estado en mi proyecto:** ✅ (con detalles a pulir)

## 1. Qué pide el perfil, en concreto
APIs REST bien diseñadas: versionado, contratos OpenAPI, validación, errores consistentes, autenticación, paginación e idempotencia.

## 2. Qué tengo hoy (evidencia)
- `app.py` → factory `create_app()`, routers registrados con `dependencies=[Depends(get_current_user)]` salvo health/auth, CORS por entorno, handler global que no filtra trazas salvo `DEBUG_ERRORS`.
- `src/presentation/schemas.py` → modelos Pydantic de request.
- `src/presentation/dependencies.py` → `HTTPBearer` + JWT (PyJWT HS256, `auth_service.py:140`).
- Versionado `/api/v1/*`; `docs/swagger.yaml` estático.
- `src/presentation/errors.py` y `src/domain/errors.py` → errores de dominio.

## 3. Brechas
- Respuestas de error con formas distintas (`{"error": {...}}` en orquestador vs `{"error": "..."}` en handler global vs `detail` de FastAPI). Adoptar **RFC 9457 (Problem Details)**.
- `/api/ai/*` (`routes.py`) sin versión y sin `response_model`.
- Sin `response_model` en endpoints del chat → OpenAPI pobre.
- `message` sin `max_length` (`schemas.py:152`).
- Sin `Idempotency-Key` en POST que crean recursos (`/tracking/visitas`).
- `swagger.yaml` estático puede divergir del OpenAPI generado.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| API1 | Problem Details uniforme (`type,title,status,detail,instance,trace_id`) | `src/presentation/errors.py`, `app.py` | Todos los 4xx/5xx tienen el mismo esquema (test parametrizado) | S |
| API2 | `response_model=OrchestratorResponse` | `schemas.py`, `orchestrator.py` | `/openapi.json` describe la respuesta | S |
| API3 | Deprecar `/api/ai/*` o moverlo a `/api/v1/ai/*` | `routes.py`, `app.py` | Header `Deprecation` | S |
| API4 | Middleware `Idempotency-Key` con Redis (TTL 24 h) | `src/presentation/middleware/idempotency.py` | Repetir POST devuelve la misma respuesta sin duplicar fila | M |
| API5 | Test de contrato: comparar `app.openapi()` con snapshot en CI | `tests/contract/test_openapi.py` | CI falla si cambia el contrato sin actualizar snapshot | S |

## 5. Teoría que debo dominar para la entrevista
- Dependencias de FastAPI (scoping, `yield` para recursos), `lifespan`, middlewares ASGI puros vs `@app.middleware("http")` (este último lee el body y rompe streaming).
- Versionado por URL vs header; compatibilidad hacia atrás.
- Paginación por cursor vs offset (cursor para tablas grandes).
- Idempotencia: GET/PUT/DELETE idempotentes; POST necesita `Idempotency-Key`.
- Códigos: 400 vs 422, 401 vs 403, 409, 429 con `Retry-After`, 503 en readiness.

## 6. Preguntas probables y respuesta guía
- *¿Cómo proteges todas las rutas salvo algunas?* Dependencias a nivel de router (lo hago en `app.py`).
- *¿Por qué tu chat devuelve 400 si el LLM falla?* Debería ser 502/503 (dependencia externa) — lo reconozco y lo corregiría.
- *¿Cómo documentas el contrato para el frontend?* OpenAPI generado + snapshot + generación de cliente TS.

## 7. Cómo lo demuestro
`/docs` con modelos de respuesta completos y un ejemplo de error Problem Details con `trace_id` que se puede buscar en App Insights.

## 8. Recursos
- https://fastapi.tiangolo.com/advanced/events/
- https://www.rfc-editor.org/rfc/rfc9457
- https://fastapi.tiangolo.com/tutorial/dependencies/
