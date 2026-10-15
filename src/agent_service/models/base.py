"""
Base para adaptadores que hablan la API chat.completions de OpenAI
===================================================================
Azure OpenAI, Azure AI Foundry y todos los proveedores compatibles
(Kimi/Moonshot, DeepSeek, Groq, OpenRouter, Ollama, vLLM…) comparten el
mismo cuerpo de petición y de respuesta. Lo único que cambia entre ellos es
cómo se construye el cliente, así que eso es lo único que las subclases
implementan.
"""

import asyncio
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

from agent_service.models.llm_settings import LLMConfig
from agent_service.telemetry.cost import estimate_cost_usd
from agent_service.telemetry.observability import run_in_context, span
from agent_service.models.illm_provider import ILLMProvider, LLMResponse, LLMToolCall

logger = logging.getLogger(__name__)


class _Observation:
    """Resultado de una llamada observada; lo rellena el llamador."""

    response: Optional[LLMResponse] = None


class _Usage:
    """Adapta el `usage` suelto de un stream a la forma que lee `_extract_usage`."""

    def __init__(self, usage: Any):
        self.usage = usage


class OpenAIChatProviderBase(ILLMProvider):
    """Implementación común; las subclases solo construyen el cliente."""

    # Cuánto espera un request de usuario a que la conexión (credencial +
    # handshake) esté lista. Los adaptadores que autentican con Entra ID
    # (az login / Managed Identity) lo alargan porque ese paso puede tardar
    # bastante más, sobre todo en frío — ver azure_agent.py / azure_foundry.py.
    _CONNECT_TIMEOUT_SECONDS = 10

    def __init__(self, config: LLMConfig):
        self._config = config
        self._client: Any = None
        self._async_client: Any = None  # derivado de _client en el primer achat()
        self._last_error: Optional[str] = None

        # Estado de la conexión en segundo plano, separado de `_client` para
        # poder "adoptar" un intento que sigue en marcha en vez de
        # descartarlo. Construir el cliente puede implicar 'az login', que en
        # frío tarda más que el timeout de un único request: si se
        # descartara el hilo al expirar, cada request fallido dispararía un
        # intento nuevo desde cero (un 'az' distinto, una credencial
        # distinta) en lugar de reutilizar el que ya está en camino.
        self._connect_lock = threading.Lock()
        self._connect_thread: Optional[threading.Thread] = None
        self._connect_result: list = []
        self._connect_error: list = []

    # ── A implementar por cada adaptador ──────────────────────────────

    def _build_client(self) -> Any:
        """
        Devuelve un cliente con la interfaz `.chat.completions.create(...)`.

        Se llama desde un hilo en segundo plano: puede bloquear sin miedo,
        el límite de espera hacia el usuario lo aplica `_ensure_client`.
        """
        raise NotImplementedError

    def get_provider_type(self) -> str:
        raise NotImplementedError

    def _invoke(self, client: Any, kwargs: Dict[str, Any]) -> Any:
        """
        Lanza la petición contra el SDK. Sobreescribible: un adaptador que
        hable otro protocolo (p. ej. los agentes de Foundry, que usan la
        Responses API) reimplementa esto y `_normalize` sin perder el
        timeout duro ni la conexión perezosa.
        """
        return client.chat.completions.create(**kwargs)

    # ── Conexión perezosa y adoptable ─────────────────────────────────

    def _ensure_client(self) -> Any:
        """
        Construye el cliente la primera vez que hace falta, en un hilo en
        segundo plano que sobrevive a un timeout de request.

        Si `_build_client()` no termina dentro de `_CONNECT_TIMEOUT_SECONDS`,
        este request devuelve "no disponible todavía" pero el hilo NO se
        cancela ni se descarta: sigue conectando. El próximo request que
        llame a `_ensure_client()` se sube al mismo hilo (o recoge su
        resultado si ya terminó) en vez de relanzar la autenticación desde
        cero — así el coste de un cold-start (p. ej. 'az login' resolviendo
        credenciales por primera vez) se paga una sola vez, no en cada
        intento fallido.
        """
        if self._client is not None:
            return self._client

        with self._connect_lock:
            # Un intento previo terminó en error: se limpia para poder
            # reintentar desde cero en este request.
            # Sin intento en curso, o el previo terminó en error: se reintenta.
            if self._connect_thread is None or self._connect_error:
                self._connect_result = []
                self._connect_error = []
                self._connect_thread = threading.Thread(
                    target=self._run_connect,
                    args=(self._connect_result, self._connect_error),
                    daemon=True,
                )
                self._connect_thread.start()

            # Se capturan junto con el hilo: otro request puede reiniciar los
            # atributos de instancia tras un fallo mientras este sigue en join(),
            # y leerlos después daría listas vacías (IndexError).
            thread = self._connect_thread
            result, error = self._connect_result, self._connect_error

        thread.join(timeout=self._CONNECT_TIMEOUT_SECONDS)

        if thread.is_alive():
            self._last_error = (
                f"Conectando a {self._config.provider} todavía en curso "
                f"(lleva más de {self._CONNECT_TIMEOUT_SECONDS}s, probablemente "
                "resolviendo credenciales en frío). Reintenta en unos segundos: "
                "el mismo intento sigue avanzando en segundo plano."
            )
            logger.warning("[LLM] %s", self._last_error)
            return None

        if error:
            self._last_error = str(error[0])
            logger.error(
                "[LLM] No se pudo conectar a %s: %s",
                self._config.provider,
                error[0],
            )
            return None

        self._client = result[0]
        self._last_error = None
        logger.info("[LLM] Conectado -> %s", self._config.describe())
        return self._client

    def _run_connect(self, result: list, error: list) -> None:
        """Cuerpo del hilo en segundo plano: construye el cliente una vez."""
        try:
            result.append(self._build_client())
        except Exception as exc:  # pylint: disable=broad-except
            error.append(exc)

    # ── Contrato ILLMProvider ─────────────────────────────────────────

    def get_model(self) -> str:
        return self._config.model

    def is_available(self) -> bool:
        return self._ensure_client() is not None

    def get_last_error(self) -> Optional[str]:
        return self._last_error

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        if client is None:
            raise RuntimeError(self._unavailable_message())

        kwargs = self._request_kwargs(messages, tools, tool_choice)
        with self._observed(kwargs) as obs:
            raw = self._create_with_timeout(client, kwargs, timeout or self._config.timeout)
            obs.response = self._to_response(raw)
        return obs.response

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """
        Versión nativa async: cliente `AsyncOpenAI` y `asyncio.timeout`.

        A diferencia del timeout por hilo de `chat`, aquí el timeout CANCELA
        la petición: la corrutina del SDK recibe CancelledError, httpx cierra
        la conexión y no queda nada consumiendo cuota en segundo plano.
        """
        aclient = await self._ensure_async_client()
        kwargs = self._request_kwargs(messages, tools, tool_choice)
        limit = timeout or self._config.timeout

        with self._observed(kwargs) as obs:
            try:
                async with asyncio.timeout(limit):
                    raw = await self._ainvoke(aclient, kwargs)
            except TimeoutError as exc:
                self._last_error = f"Timeout de {limit:.0f}s sin respuesta"
                raise TimeoutError(
                    f"El proveedor '{self._config.provider}' no respondió en {limit:.0f}s. "
                    "Verifica el endpoint, el modelo/deployment y la conectividad."
                ) from exc
            except Exception as exc:
                self._last_error = str(exc)
                raise
            obs.response = self._to_response(raw)
        return obs.response

    async def astream_chat(
        self,
        messages: List[Dict[str, Any]],
        on_delta: Callable[[str], None],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """
        `achat` con `stream=True`: el texto sale por `on_delta` según llega y
        las tool calls, que también llegan troceadas, se ensamblan para
        devolver la misma respuesta completa que `achat`.

        El timeout cubre el stream entero, no solo el primer fragmento.
        """
        aclient = await self._ensure_async_client()
        kwargs = self._request_kwargs(messages, tools, tool_choice)
        kwargs["stream"] = True
        # Sin esto el stream no trae `usage` y se pierden tokens y costo.
        kwargs["stream_options"] = {"include_usage": True}
        limit = timeout or self._config.timeout

        with self._observed(kwargs) as obs:
            try:
                async with asyncio.timeout(limit):
                    stream = await self._ainvoke(aclient, kwargs)
                    obs.response = await self._consume_stream(stream, on_delta)
            except TimeoutError as exc:
                self._last_error = f"Timeout de {limit:.0f}s sin respuesta"
                raise TimeoutError(
                    f"El proveedor '{self._config.provider}' no respondió en {limit:.0f}s. "
                    "Verifica el endpoint, el modelo/deployment y la conectividad."
                ) from exc
            except Exception as exc:
                self._last_error = str(exc)
                raise
        return obs.response

    @staticmethod
    async def _consume_stream(stream: Any, on_delta: Callable[[str], None]) -> LLMResponse:
        """Recorre los chunks de chat.completions y arma la LLMResponse."""
        content: List[str] = []
        # Cada tool call llega en varios chunks con el mismo `index`: el
        # primero trae id y nombre, los siguientes trozos de los argumentos.
        calls: Dict[int, Dict[str, str]] = {}
        reasoning: List[Any] = []
        model: Optional[str] = None
        usage: Any = None

        async for chunk in stream:
            model = getattr(chunk, "model", None) or model
            usage = getattr(chunk, "usage", None) or usage
            # Azure manda un primer chunk de filtros de contenido y el de
            # `usage` llega al final: los dos sin choices.
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                text = getattr(delta, "content", None)
                if text:
                    content.append(text)
                    on_delta(text)
                for tc in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(
                        getattr(tc, "index", 0) or 0, {"id": "", "name": "", "arguments": ""}
                    )
                    slot["id"] = getattr(tc, "id", None) or slot["id"]
                    function = getattr(tc, "function", None)
                    if function is not None:
                        slot["name"] += getattr(function, "name", None) or ""
                        slot["arguments"] += getattr(function, "arguments", None) or ""
                details = getattr(delta, "reasoning_details", None)
                if details:
                    reasoning.extend(details if isinstance(details, list) else [details])

        response = LLMResponse(
            content="".join(content) or None,
            tool_calls=[
                LLMToolCall(id=c["id"], name=c["name"], arguments=c["arguments"] or "{}")
                for _, c in sorted(calls.items())
            ],
            model=model,
            reasoning_details=reasoning or None,
        )
        response.input_tokens, response.output_tokens = OpenAIChatProviderBase._extract_usage(
            _Usage(usage)
        )
        return response

    # ── Petición (sobreescribible por adaptadores con otro protocolo) ──

    def _request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: str,
    ) -> Dict[str, Any]:
        """Cuerpo de la petición; común a `chat` y `achat`."""
        kwargs: Dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if self._config.headers:
            kwargs["extra_headers"] = self._config.headers
        if self._config.reasoning_effort:
            kwargs["reasoning_effort"] = self._config.reasoning_effort
        if self._config.max_tokens:
            # Azure (gpt-5*, o*) solo acepta max_completion_tokens; el resto
            # de proveedores compatibles sigue usando max_tokens.
            key = "max_tokens" if self._config.kind == "openai_compatible" else "max_completion_tokens"
            kwargs[key] = self._config.max_tokens
        if self._config.extra_body:
            kwargs["extra_body"] = self._config.extra_body
        return kwargs

    async def _ainvoke(self, aclient: Any, kwargs: Dict[str, Any]) -> Any:
        """Equivalente async de `_invoke`."""
        return await aclient.chat.completions.create(**kwargs)

    def _unavailable_message(self) -> str:
        return (
            f"Proveedor LLM '{self._config.provider}' no disponible: "
            f"{self._last_error or 'sin credenciales configuradas'}"
        )

    # ── Cliente async ─────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Cierra el `AsyncOpenAI` (y su pool httpx) al apagar el servicio."""
        aclient, self._async_client = self._async_client, None
        if aclient is not None:
            await aclient.close()

    async def _ensure_async_client(self) -> Any:
        """
        Cliente async, derivado del síncrono la primera vez.

        La conexión (credenciales, lectura de la definición del agente) sigue
        pasando por `_ensure_client`, con su hilo adoptable y su tope de
        espera; se lanza en `to_thread` para no bloquear el event loop.
        Solo ocurre una vez por proceso.
        """
        if self._async_client is not None:
            return self._async_client

        client = await asyncio.to_thread(self._ensure_client)
        if client is None:
            raise RuntimeError(self._unavailable_message())

        if self._async_client is None:
            self._async_client = self._build_async_client(client)
        return self._async_client

    def _build_async_client(self, sync_client: Any) -> Any:
        """
        `AsyncOpenAI` con la misma base_url, cabeceras y autenticación que el
        cliente síncrono. Sirve tal cual para los compatibles OpenAI y para
        Foundry, cuyo cliente autentica con un *callable* que devuelve el
        token de Entra ID (lo crea `AIProjectClient.get_openai_client`): ese
        callable es síncrono y puede refrescar el token por red, así que se
        envuelve en `to_thread`. El proveedor de tokens de azure-identity
        cachea, así que en régimen normal la llamada es inmediata.
        """
        from openai import AsyncOpenAI

        token_provider = getattr(sync_client, "_api_key_provider", None)
        if token_provider is not None:

            async def api_key() -> str:
                return await asyncio.to_thread(token_provider)

        else:
            api_key = sync_client.api_key

        return AsyncOpenAI(
            api_key=api_key,
            base_url=str(sync_client.base_url),
            default_headers=dict(getattr(sync_client, "_custom_headers", {}) or {}),
            default_query=dict(getattr(sync_client, "_custom_query", {}) or {}),
            timeout=self._config.timeout,
            max_retries=sync_client.max_retries,
        )

    # ── Observabilidad ────────────────────────────────────────────────

    def _to_response(self, raw: Any) -> LLMResponse:
        response = self._normalize(raw)
        response.input_tokens, response.output_tokens = self._extract_usage(raw)
        return response

    @contextmanager
    def _observed(self, kwargs: Dict[str, Any]) -> Iterator[_Observation]:
        """
        Envuelve una llamada al modelo en un span `chat <modelo>` con
        atributos GenAI de OpenTelemetry y deja un log `llm.call` con tokens y
        latencia. El llamador asigna `obs.response` al terminar. Lo usan
        `chat` y `achat` de todos los adaptadores.
        """
        model = self.get_model()
        with span(
            f"chat {model}",
            **{
                "gen_ai.operation.name": "chat",
                "gen_ai.system": self.get_provider_type(),
                "gen_ai.request.model": model,
                "gen_ai.request.tool_count": len(kwargs.get("tools") or []) or None,
            },
        ) as current:
            obs = _Observation()
            start = time.perf_counter()
            outcome = "error"
            try:
                yield obs
                if obs.response is not None:
                    outcome = "tool_calls" if obs.response.has_tool_calls else "text"
            except TimeoutError:
                outcome = "timeout"
                raise
            finally:
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
                response = obs.response
                in_tok = response.input_tokens if response else None
                out_tok = response.output_tokens if response else None
                if response is not None:
                    response.latency_ms = latency_ms
                    current.set_attribute("gen_ai.response.model", response.model or model)
                    if in_tok is not None:
                        current.set_attribute("gen_ai.usage.input_tokens", in_tok)
                    if out_tok is not None:
                        current.set_attribute("gen_ai.usage.output_tokens", out_tok)
                    if response.tool_calls:
                        current.set_attribute(
                            "gen_ai.response.tool_calls",
                            ",".join(tc.name for tc in response.tool_calls),
                        )
                cost_usd = estimate_cost_usd(in_tok, out_tok)
                logger.info(
                    "[LLM] %s model=%s outcome=%s tokens_in=%s tokens_out=%s cost_usd=%s %.1f ms",
                    self.get_provider_type(), model, outcome, in_tok, out_tok, cost_usd, latency_ms,
                    extra={
                        "event": "llm.call",
                        "llm_provider": self.get_provider_type(),
                        "llm_model": model,
                        "llm_outcome": outcome,
                        "tokens_in": in_tok,
                        "tokens_out": out_tok,
                        "cost_usd": cost_usd,
                        "latency_ms": latency_ms,
                    },
                )

    @staticmethod
    def _extract_usage(raw: Any):
        """
        (input_tokens, output_tokens) del `usage` de la respuesta.

        chat.completions informa prompt_tokens/completion_tokens; la
        Responses API (agentes de Foundry), input_tokens/output_tokens.
        """
        usage = getattr(raw, "usage", None)
        if usage is None:
            return None, None
        tokens_in = getattr(usage, "prompt_tokens", None)
        if tokens_in is None:
            tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "completion_tokens", None)
        if tokens_out is None:
            tokens_out = getattr(usage, "output_tokens", None)
        return (
            tokens_in if isinstance(tokens_in, int) else None,
            tokens_out if isinstance(tokens_out, int) else None,
        )

    # ── Utilidades ────────────────────────────────────────────────────

    def _create_with_timeout(self, client: Any, kwargs: Dict[str, Any], timeout: float):
        """
        Timeout duro por daemon thread: algunos SDK se quedan colgados pese a
        su propio timeout, y un request colgado bloquea un worker de uvicorn.
        """
        result_holder: list = []
        error_holder: list = []

        def _do_call():
            try:
                result_holder.append(self._invoke(client, kwargs))
            except Exception as exc:  # pylint: disable=broad-except
                error_holder.append(exc)

        # run_in_context: el hilo hereda request_id y el span activo, así la
        # petición HTTP del SDK cuelga del span `chat <modelo>`.
        thread = threading.Thread(target=run_in_context(_do_call), daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            self._last_error = f"Timeout de {timeout:.0f}s sin respuesta"
            logger.error(
                "[LLM] TIMEOUT: %s no respondió en %.0fs",
                self._config.provider,
                timeout,
            )
            raise TimeoutError(
                f"El proveedor '{self._config.provider}' no respondió en {timeout:.0f}s. "
                "Verifica el endpoint, el modelo/deployment y la conectividad."
            )
        if error_holder:
            self._last_error = str(error_holder[0])
            raise error_holder[0]

        return result_holder[0]

    def _normalize(self, raw: Any) -> LLMResponse:
        """Convierte la respuesta del SDK en la respuesta normalizada del port."""
        choice = raw.choices[0]
        message = choice.message

        tool_calls: List[LLMToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            function = getattr(tc, "function", None)
            tool_calls.append(
                LLMToolCall(
                    id=getattr(tc, "id", "") or "",
                    name=getattr(function, "name", "") or "",
                    arguments=getattr(function, "arguments", "") or "{}",
                )
            )

        return LLMResponse(
            content=getattr(message, "content", None),
            tool_calls=tool_calls,
            model=getattr(raw, "model", None),
            reasoning_details=getattr(message, "reasoning_details", None),
            raw=raw,
        )
