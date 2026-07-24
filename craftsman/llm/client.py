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
        if not get_settings().anthropic_api_key:
            # fail at construction with a pointer, not with a 401 mid-pipeline
            raise ValueError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty — set a key, "
                "or use LLM_PROVIDER=ollama (local, $0) or LLM_PROVIDER=openai"
            )
        from craftsman.llm.anthropic_impl import AnthropicClient

        return AnthropicClient()
    if provider == "openai":
        from craftsman.llm.openai_impl import OpenAIClient

        return OpenAIClient()
    if provider == "ollama":
        from craftsman.llm.ollama_impl import OllamaClient

        return OllamaClient()
    if provider == "mock":
        from craftsman.llm.mock_impl import MockLLM

        return MockLLM()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
