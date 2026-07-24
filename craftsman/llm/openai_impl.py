"""OpenAI (ChatGPT) implementation via the Chat Completions API — raw httpx, no
SDK dependency, mirroring ollama_impl. `OPENAI_BASE_URL` may point at any
OpenAI-compatible endpoint (Azure gateway, Groq, LiteLLM, vLLM, ...), so the
request adapts to two known dialect gaps instead of assuming api.openai.com:
reasoning models (gpt-5/o-series) reject non-default `temperature`, and older
compat servers only know `max_tokens`, not `max_completion_tokens`. Both are
negotiated on a 400 without consuming a validation retry."""

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from craftsman.core.config import get_settings

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 2


class OpenAIClient:
    def __init__(
        self,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        settings = get_settings()
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key
        self.model = model or settings.openai_model
        # test seam: httpx.MockTransport in unit tests, None (real network) in prod
        self._transport = transport

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
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        headers = {}
        if self.api_key:  # local compat servers are keyless
            headers["Authorization"] = f"Bearer {self.api_key}"

        send_temperature = True
        tokens_param = "max_completion_tokens"
        last_error: Exception | None = None
        attempts = 0
        async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
            while attempts <= MAX_RETRIES:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "response_format": response_format,
                    tokens_param: max_tokens,
                }
                if send_temperature:
                    payload["temperature"] = temperature
                resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                if resp.status_code == 400:
                    if send_temperature and "temperature" in resp.text:
                        send_temperature = False
                        continue
                    if tokens_param == "max_completion_tokens" and tokens_param in resp.text:
                        tokens_param = "max_tokens"
                        continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                attempts += 1
                try:
                    return schema.model_validate(json.loads(content))
                except (ValidationError, json.JSONDecodeError) as e:
                    last_error = e
                    messages = messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"Validation failed, fix and re-emit:\n{e}"},
                    ]
        raise RuntimeError(f"structured() failed after {MAX_RETRIES + 1} attempts: {last_error}")
