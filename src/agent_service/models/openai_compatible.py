"""
Adaptador: cualquier API compatible con OpenAI
===============================================
Cubre OpenAI, Kimi (Moonshot), DeepSeek, Groq, OpenRouter, Mistral, Together,
Ollama, vLLM y cualquier gateway que exponga /chat/completions con `tools`.
Lo único que cambia entre ellos es base_url + api_key + model, así que no hace
falta un adaptador por proveedor: basta un preset en llm_settings.py o una
entrada en llm_providers.json.

Variables:
    LLM_PROVIDER   Preset a usar (kimi, deepseek, groq, openrouter, ollama…)
    LLM_BASE_URL   Sobreescribe la base_url del preset
    LLM_API_KEY    Sobreescribe la key (si no, se lee la del preset: p. ej.
                   MOONSHOT_API_KEY para kimi)
    LLM_MODEL      Sobreescribe el modelo
"""

from typing import Any

from agent_service.models.base import OpenAIChatProviderBase


class OpenAICompatibleProvider(OpenAIChatProviderBase):
    """Cliente OpenAI apuntado a un base_url arbitrario"""

    def get_provider_type(self) -> str:
        return "openai_compatible"

    def _build_client(self) -> Any:
        from openai import OpenAI

        cfg = self._config

        if not cfg.base_url:
            raise ValueError(
                f"El proveedor '{cfg.provider}' no tiene base_url. "
                "Define LLM_BASE_URL o añádela al preset en llm_providers.json."
            )

        # Los servidores locales (Ollama, vLLM) ignoran la key, pero el SDK
        # exige un string no vacío.
        api_key = cfg.api_key or "not-needed"

        return OpenAI(
            api_key=api_key,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )
