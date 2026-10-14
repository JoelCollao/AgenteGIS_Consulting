# asyncio para entrevista técnica

## Respuesta de 1 minuto

> "Trabajo en un agente GIS: un servicio FastAPI con LangGraph que recibe preguntas, consulta a un LLM y ejecuta análisis espaciales llamando a un backend por HTTP. Como casi todo es espera de red, el camino de cada petición es asíncrono: el grafo se ejecuta con `ainvoke`, el LLM con `AsyncOpenAI` y el backend con `httpx.AsyncClient`.
>
> De asyncio uso cuatro piezas. `asyncio.gather` con un `Semaphore` para ejecutar en paralelo las tools que pide el modelo en una ronda, máximo 4 a la vez y conservando el orden; cada tool convierte sus errores en un resultado, así que una que falla no cancela a las demás. `asyncio.timeout` en la llamada al LLM para que, si no responde, la petición se cancele de verdad. `asyncio.to_thread` para sacar del event loop lo bloqueante: credenciales de Azure y cálculo de geometrías con Shapely. Y `ContextVar` para que el JWT y el request-id lleguen a cada tool. Además puse un tope global de conexiones con `httpx.Limits`, porque el semáforo es por petición, y cierro los clientes async en el shutdown."

## Si preguntan "¿y en el backend?"

> "El backend usa SQLAlchemy síncrono contra PostGIS, así que sus endpoints `async` delegan cada consulta con `asyncio.to_thread`. Para el tracking en tiempo real usa Redis async con pub/sub y una tarea de fondo con `asyncio.create_task` que reenvía eventos a los WebSockets, guardando la referencia a la tarea y cancelándola al apagar."

## Preguntas y respuestas (nivel junior)

**1. ¿Qué es asyncio en una frase?**
Una forma de que un solo hilo atienda muchas tareas que pasan tiempo esperando (red, disco), cambiando de una a otra en cada `await`.

**2. ¿Qué diferencia hay entre concurrencia y paralelismo?**
Concurrencia: varias tareas avanzan intercalándose (asyncio). Paralelismo: varias tareas se ejecutan literalmente al mismo tiempo en varios núcleos. asyncio da concurrencia, no paralelismo de CPU.

**3. ¿Qué es una corrutina?**
Una función `async def`. Llamarla no la ejecuta: devuelve un objeto que hay que `await`-ear (o convertir en tarea) para que corra.

**4. ¿Qué hace `await`?**
Dice "espera este resultado y, mientras tanto, deja que el event loop atienda otras cosas".

**5. ¿Qué es el event loop y quién lo crea aquí?**
El bucle que decide qué corrutina avanza en cada momento. En este proyecto lo crea uvicorn al arrancar; por eso no hay `asyncio.run` en el código.

**6. ¿Para qué sirve `asyncio.gather` en el agente?**
Para lanzar todas las tools de una ronda a la vez y esperar a que terminen todas. Tarda lo que la más lenta, no la suma.

**7. ¿Por qué añadir un `Semaphore` si `gather` ya funciona?**
Porque si el modelo pidiera 20 tools, se lanzarían 20 peticiones al backend de golpe. El semáforo deja pasar solo 4 a la vez.

**8. ¿Qué pasa si una tool lanza una excepción dentro de `gather`?**
Por defecto `gather` propaga la primera excepción. Aquí no ocurre porque `_run_tool` y el registro de skills capturan los errores y devuelven `{"type": "error", ...}`, que el modelo puede leer y corregir.

**9. ¿Por qué `asyncio.timeout` y no solo el timeout del SDK?**
Porque garantiza un límite total de tiempo y, al vencer, **cancela** la corrutina: httpx cierra la conexión y no queda una petición consumiendo cuota en segundo plano.

**10. ¿Qué pasa si llamo a `time.sleep(2)` dentro de un `async def`?**
Se congela todo el servicio 2 segundos: ninguna otra petición avanza. Hay que usar `await asyncio.sleep(2)`.

**11. ¿Cuándo uso `asyncio.to_thread`?**
Cuando tengo que llamar a algo síncrono que tarda: una librería sin versión async (azure-identity), una consulta con SQLAlchemy síncrono (backend) o un cálculo pesado (Shapely). Lo mando a un hilo y hago `await` del resultado.

**12. ¿`to_thread` sirve para cálculos muy pesados de CPU?**
Para cálculos cortos sí. Para cálculos largos, el GIL de Python limita el paralelismo; ahí conviene un `ProcessPoolExecutor`.

**13. ¿Cómo llega el JWT del usuario a cada tool si corren en paralelo?**
Con un `ContextVar`. Cada tarea de asyncio y cada `to_thread` copian el contexto del momento en que se crean, así que cada tool ve el token de su propia petición.

**14. ¿Por qué hay que cerrar `httpx.AsyncClient` y `AsyncOpenAI`?**
Mantienen un pool de conexiones abiertas. Si no se cierran, quedan sockets abiertos y avisos de "unclosed client" al apagar.

**15. ¿Qué diferencia hay entre `gather` y `TaskGroup`?**
`TaskGroup` (Python 3.11) cancela todas las tareas si una falla, y lanza los errores juntos. `gather` sin `return_exceptions` propaga la primera excepción pero deja seguir a las demás. Aquí se eligió `gather` porque las tools nunca lanzan y queremos todos los resultados.

**16. ¿Qué cuidado hay con `asyncio.create_task`?**
Guardar una referencia a la tarea (el loop solo guarda una débil y podría desaparecer) y esperarla o cancelarla al apagar. El backend lo hace así en `actualizar_ubicacion_command.py` y `connection_manager.py`.
