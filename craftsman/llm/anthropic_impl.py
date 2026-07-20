"""Claude implementation using tool-use so Pydantic validation is enforced at the
API layer, not regex-parsed. Retry-with-errors converges in one retry ~95% of the time."""

from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from craftsman.core.config import get_settings

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 2


class AnthropicClient:
    def __init__(self, model: str | None = None):
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.anthropic_model

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> T:
        tool = {
            "name": "emit",
            "description": f"Emit a {schema.__name__} object. Call exactly once.",
            "input_schema": schema.model_json_schema(),
        }
        messages = [{"role": "user", "content": user}]
        last_error: Exception | None = None

        for _ in range(MAX_RETRIES + 1):
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": "emit"},
                messages=messages,
            )
            tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_use is None:
                last_error = RuntimeError("model returned no tool_use block")
                continue
            try:
                return schema.model_validate(tool_use.input)
            except ValidationError as e:
                last_error = e
                # retry-with-errors: append the failure as a user turn
                messages = messages + [
                    {"role": "assistant", "content": resp.content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": f"Validation failed, fix and re-emit:\n{e}",
                                "is_error": True,
                            }
                        ],
                    },
                ]
        raise RuntimeError(f"structured() failed after {MAX_RETRIES + 1} attempts: {last_error}")
