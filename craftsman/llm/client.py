"""Provider-agnostic LLM interface. Everything structured, nothing free-form."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> T: ...


def get_llm() -> LLMClient:
    from craftsman.core.config import get_settings

    provider = get_settings().llm_provider
    if provider == "anthropic":
        from craftsman.llm.anthropic_impl import AnthropicClient

        return AnthropicClient()
    if provider == "ollama":
        from craftsman.llm.ollama_impl import OllamaClient

        return OllamaClient()
    if provider == "mock":
        from craftsman.llm.mock_impl import MockLLM

        return MockLLM()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
