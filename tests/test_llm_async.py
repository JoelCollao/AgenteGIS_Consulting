"""
Tests de achat() en los adaptadores
====================================
Lo importante de la versión async es que el timeout CANCELE la petición (el
timeout por hilo de `chat` la dejaba corriendo) y que el cliente async
herede la autenticación del síncrono, incluido el token de Entra ID de
Foundry. Sin red: clientes falsos o construidos sin llamar.
"""

import asyncio

import pytest

from agent_service.models.llm_settings import (
    KIND_AZURE_AGENT,
    KIND_AZURE_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    LLMConfig,
)
from agent_service.models.azure_agent import AzureAgentProvider
from agent_service.models.azure_openai import AzureOpenAIProvider
from agent_service.models.misconfigured import MisconfiguredProvider
from agent_service.models.openai_compatible import OpenAICompatibleProvider


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _completion(content="hola"):
    message = _Obj(content=content, tool_calls=[], reasoning_details=None)
    return _Obj(
        choices=[_Obj(message=message)],
        model="m",
        usage=_Obj(prompt_tokens=11, completion_tokens=3),
    )


class _AsyncCompletions:
    def __init__(self, result=None, delay=0.0):
        self.result = result
        self.delay = delay
        self.kwargs = None
        self.cancelled = False

    async def create(self, **kwargs):
        self.kwargs = kwargs
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


def _compatible(**overrides) -> OpenAICompatibleProvider:
    base = {
        "provider": "fake",
        "kind": KIND_OPENAI_COMPATIBLE,
        "model": "m",
        "base_url": "https://example.invalid/v1",
        "api_key": "k",
    }
    base.update(overrides)
    return OpenAICompatibleProvider(LLMConfig(**base))


def _with_async_client(provider, completions):
    provider._client = object()
    provider._async_client = _Obj(chat=_Obj(completions=completions))
    return provider


def test_achat_devuelve_respuesta_con_tokens():
    completions = _AsyncCompletions(result=_completion("hola"))
    provider = _with_async_client(_compatible(), completions)

    response = asyncio.run(provider.achat([{"role": "user", "content": "x"}], tools=[{"t": 1}]))

    assert response.content == "hola"
    assert response.input_tokens == 11
    assert response.output_tokens == 3
    assert completions.kwargs["tools"] == [{"t": 1}]
    assert completions.kwargs["tool_choice"] == "auto"


def test_timeout_de_achat_cancela_la_peticion():
    completions = _AsyncCompletions(result=_completion(), delay=5)
    provider = _with_async_client(_compatible(), completions)

    async def _go():
        start = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError, match="no respondió"):
            await provider.achat([{"role": "user", "content": "x"}], timeout=0.05)
        return asyncio.get_running_loop().time() - start

    elapsed = asyncio.run(_go())

    assert elapsed < 1
    assert completions.cancelled, "la petición del SDK debía cancelarse"
    assert "Timeout" in provider.get_last_error()


def test_cliente_async_derivado_conserva_base_url_y_cabeceras():
    from openai import AsyncOpenAI, OpenAI

    provider = _compatible()
    sync = OpenAI(api_key="k", base_url="https://example.invalid/v1", default_headers={"x-a": "1"})

    aclient = provider._build_async_client(sync)

    assert isinstance(aclient, AsyncOpenAI)
    assert str(aclient.base_url) == "https://example.invalid/v1/"
    assert aclient.api_key == "k"
    assert aclient._custom_headers["x-a"] == "1"


def test_cliente_async_usa_el_token_de_entra_id_del_sincrono():
    """Foundry autentica con un callable síncrono; el async debe usarlo."""
    from openai import OpenAI

    calls = []

    def token_provider():
        calls.append(1)
        return "token-entra"

    sync = OpenAI(api_key=token_provider, base_url="https://x.services.ai.azure.com/openai/v1")
    aclient = _compatible()._build_async_client(sync)

    token = asyncio.run(aclient._api_key_provider())
    assert token == "token-entra"
    assert calls == [1]


def test_azure_openai_construye_async_azure():
    from openai import AsyncAzureOpenAI

    provider = AzureOpenAIProvider(
        LLMConfig(
            provider="azure",
            kind=KIND_AZURE_OPENAI,
            model="dep",
            api_key="k",
            endpoint="https://r.openai.azure.com/",
            api_version="2024-10-21",
        )
    )
    assert isinstance(provider._build_async_client(None), AsyncAzureOpenAI)


def test_agente_foundry_achat_usa_responses_api():
    created = {}

    class _Responses:
        async def create(self, **kwargs):
            created.update(kwargs)
            return _Obj(output=[], output_text="hola", model="gpt", usage=_Obj(input_tokens=5, output_tokens=2))

    provider = AzureAgentProvider(
        LLMConfig(
            provider="azure_agent",
            kind=KIND_AZURE_AGENT,
            model="gpt",
            project_endpoint="https://r.services.ai.azure.com/api/projects/p",
            agent_name="geo",
        )
    )
    provider._client = object()
    provider._async_client = _Obj(responses=_Responses())

    response = asyncio.run(provider.achat([{"role": "user", "content": "hola"}]))

    assert response.content == "hola"
    assert response.input_tokens == 5
    assert created["input"] == [{"role": "user", "content": "hola"}]
    assert created["tool_choice"] == "none"  # sin tools = turno de narración
    assert "tools" not in created and "model" not in created


def test_achat_por_defecto_del_puerto_delega_en_chat():
    provider = MisconfiguredProvider("falta LLM_MODEL")
    with pytest.raises(RuntimeError, match="falta LLM_MODEL"):
        asyncio.run(provider.achat([{"role": "user", "content": "x"}]))


# ---------------------------------------------------------------------------
# reasoning_effort
# ---------------------------------------------------------------------------


def test_reasoning_effort_viaja_en_chat_completions():
    kwargs = _compatible(reasoning_effort="low")._request_kwargs([], None, "auto")
    assert kwargs["reasoning_effort"] == "low"


def test_sin_reasoning_effort_no_se_envia():
    assert "reasoning_effort" not in _compatible()._request_kwargs([], None, "auto")


def test_agente_foundry_no_envia_reasoning_effort():
    provider = AzureAgentProvider(
        LLMConfig(
            provider="azure_agent", kind=KIND_AZURE_AGENT, model="gpt",
            agent_name="geo", reasoning_effort="low",
        )
    )
    # El endpoint del agente rechaza lo que pertenece a su definición
    assert "reasoning_effort" not in provider._request_kwargs([], None, "auto")


def test_reasoning_effort_desde_entorno(monkeypatch):
    from agent_service.models.llm_settings import load_llm_config

    monkeypatch.setenv("LLM_PROVIDER", "azure_foundry")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
    monkeypatch.setenv("LLM_REASONING_EFFORT", " LOW ")
    assert load_llm_config().reasoning_effort == "low"

    monkeypatch.setenv("LLM_REASONING_EFFORT", "rapido")
    with pytest.raises(ValueError, match="minimal, low, medium, high"):
        load_llm_config()
