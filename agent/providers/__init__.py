from __future__ import annotations

from agent.providers.base import LLMProvider, LLMResponse, ToolCall
from config.settings import Settings


def create_provider(settings: Settings) -> LLMProvider:
    """Instantiate the LLM provider configured in settings."""
    name = settings.llm_provider.lower()

    if name == "anthropic":
        from agent.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    if name == "openai":
        from agent.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)

    if name == "gemini":
        from agent.providers.gemini_provider import GeminiProvider
        return GeminiProvider(api_key=settings.google_ai_api_key, model=settings.gemini_model)

    raise ValueError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
        "Choose from: anthropic, openai, gemini"
    )


__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "create_provider"]
