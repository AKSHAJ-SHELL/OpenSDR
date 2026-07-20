"""Local-model fallback ($0 mode) via Ollama's JSON-schema constrained output."""

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from craftsman.core.config import get_settings

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 2


class OllamaClient:
    def __init__(self, model: str | None = None):
        settings = get_settings()
        self.base_url = settings.ollama_base_url
        self.model = model or settings.ollama_model

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120) as client:
            for _ in range(MAX_RETRIES + 1):
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "format": schema.model_json_schema(),
                        "stream": False,
                        "options": {"temperature": temperature, "num_predict": max_tokens},
                    },
                )
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
                try:
                    return schema.model_validate(json.loads(content))
                except (ValidationError, json.JSONDecodeError) as e:
                    last_error = e
                    messages = messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"Validation failed, fix and re-emit:\n{e}"},
                    ]
        raise RuntimeError(f"structured() failed after {MAX_RETRIES + 1} attempts: {last_error}")
