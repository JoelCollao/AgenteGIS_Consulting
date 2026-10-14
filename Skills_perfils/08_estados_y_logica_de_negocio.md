# Estados y lógica de negocio
**Requisito(s) del perfil:** F3 — lógica de negocio end-to-end y manejo de estados de procesos complejos.
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Modelar procesos de varios pasos (p. ej. onboarding, evaluación crediticia) con estados explícitos, transiciones válidas, persistencia, reintentos seguros (idempotencia), compensación ante fallos parciales y pausas para aprobación humana.

## 2. Qué tengo hoy (evidencia)
- `src/domain/entities/visita_validacion.py:20` → `EstadoVisita(str, Enum)` (`EN_CURSO`, `VALIDADA`, `FALLIDA`, `CANCELADA`) con métodos de transición que validan el estado actual (líneas ~95-126). Máquina de estados de dominio real.
- `src/domain/services/validador_proximidad.py` → regla de negocio pura (llegada a destino por distancia).
- `src/application/commands/` (`iniciar_visita`, `validar_llegada`, `actualizar_ubicacion`) y `queries/` → CQRS ligero.
- `scripts/migrations/001-003_*.sql` → esquema versionado del tracking.
- Flujo IA: `MultiAgentState` (`state.py`) solo vive durante la request; sin checkpointer.

## 3. Brechas
- Sin idempotency keys en comandos (`POST /tracking/visitas` repetido crea duplicados).
- Sin outbox: el evento a Redis pub/sub y la escritura en PostGIS no son atómicos.
- Sin workflows largos ni HITL en el flujo agéntico.
- Sin control de concurrencia optimista (versión) en entidades.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| S1 | `Idempotency-Key` en comandos (tabla `idempotency(key, user, response, created_at)`) | middleware, migración | Doble POST → misma respuesta, una fila | M |
| S2 | Columna `version` + `UPDATE ... WHERE version = :v` en visitas | repositorio tracking | 409 en conflicto (test) | S |
| S3 | Transactional outbox: insertar evento en `outbox` en la misma transacción; publicador async a Redis | `tracking_repository.py`, worker | Caída entre DB y Redis no pierde eventos | M |
| S4 | LangGraph con checkpointer + `interrupt()` para confirmar geoprocesos que crean capas o tocan datos | `langgraph_flow.py` | Flujo pausado sobrevive a reinicio y se reanuda con `Command(resume=...)` | M |
| S5 | Ejemplo de workflow con compensación (saga): "crear capa derivada → publicar en GeoServer → registrar en catálogo"; si falla publicar, borrar capa | `src/application/workflows/` | Test de fallo intermedio deja estado consistente | M |
| S6 | Tests de transiciones inválidas de `EstadoVisita` (property-based con `hypothesis`) | `tests/unit/` | Ninguna transición ilegal pasa | S |

## 5. Teoría que debo dominar para la entrevista
- Máquina de estados explícita: estados, eventos, guardas, efectos; prohibir transiciones ilegales en el dominio, no en el controlador.
- Idempotencia: clave por operación de negocio; almacenar respuesta; at-least-once + consumidores idempotentes.
- Outbox + relay vs two-phase commit.
- Sagas: orquestadas vs coreografiadas; compensación ≠ rollback.
- Workflows durables: LangGraph checkpointer, Durable Functions, Temporal. En banca, HITL para decisiones con impacto (aprobación de crédito).
- Agentes + reglas: el LLM **extrae y propone**; reglas deterministas **deciden** y validan.

## 6. Preguntas probables y respuesta guía
- *¿Cómo evitas que un reintento cree dos solicitudes de crédito?* Idempotency-Key + constraint único.
- *¿Dónde pondrías la lógica de negocio en un sistema con agentes?* En el dominio, determinista y testeable; el agente es un adaptador que llama casos de uso (mi `visita_validacion.py` es el ejemplo).

## 7. Cómo lo demuestro
Diagrama de estados de `EstadoVisita` + demo de un flujo LangGraph con `interrupt` reanudado tras reiniciar.

## 8. Recursos
- https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/
- https://microservices.io/patterns/data/transactional-outbox.html
- https://learn.microsoft.com/azure/architecture/reference-architectures/saga/saga
