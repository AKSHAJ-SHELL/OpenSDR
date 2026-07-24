"""OpenAI provider units + get_llm dispatch. No live network anywhere — every
request goes through httpx.MockTransport via OpenAIClient's transport seam.
"""

import json

import httpx
import pytest
from pydantic import BaseModel

from craftsman.core.config import get_settings
from craftsman.llm.client import get_llm
from craftsman.llm.openai_impl import OpenAIClient


class Verdict(BaseModel):
    label: str
    confidence: float


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _use_provider(monkeypatch, provider: str, **env):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _settings_cache(monkeypatch):
    # monkeypatch restores env after each test; the cache must be dropped on
    # both sides so no test sees another's Settings
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------- OpenAIClient


@pytest.mark.asyncio
async def test_openai_parses_valid_structured_response():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"label": "interested", "confidence": 0.9}'))

    client = OpenAIClient(model="gpt-test", transport=_transport(handler))
    out = await client.structured(system="s", user="u", schema=Verdict)
    assert out == Verdict(label="interested", confidence=0.9)
    # one request, carrying the schema-constrained response_format
    assert len(seen) == 1
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["response_format"]["json_schema"]["name"] == "Verdict"
    assert seen[0]["max_completion_tokens"] == 1024
    assert seen[0]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_openai_retry_with_errors_then_success():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=_completion("not json at all"))
        return httpx.Response(200, json=_completion('{"label": "ok", "confidence": 1.0}'))

    client = OpenAIClient(model="gpt-test", transport=_transport(handler))
    out = await client.structured(system="s", user="u", schema=Verdict)
    assert out.label == "ok"
    # the second request carries the failed attempt + validation error as turns
    assert len(calls) == 2
    assert calls[1]["messages"][2]["content"] == "not json at all"
    assert "Validation failed" in calls[1]["messages"][3]["content"]


@pytest.mark.asyncio
async def test_openai_exhausted_retries_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("garbage"))

    client = OpenAIClient(model="gpt-test", transport=_transport(handler))
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        await client.structured(system="s", user="u", schema=Verdict)


@pytest.mark.asyncio
async def test_openai_drops_temperature_for_reasoning_models():
    """gpt-5/o-series reject non-default temperature with a 400; the client must
    renegotiate without it — and the renegotiation must not consume a retry."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if "temperature" in calls[-1]:
            return httpx.Response(
                400,
                json={"error": {"message": "Unsupported parameter: 'temperature'"}},
            )
        if len([c for c in calls if "temperature" not in c]) == 1:
            return httpx.Response(200, json=_completion("still not json"))
        return httpx.Response(200, json=_completion('{"label": "ok", "confidence": 0.5}'))

    client = OpenAIClient(model="gpt-5-test", transport=_transport(handler))
    out = await client.structured(system="s", user="u", schema=Verdict)
    assert out.label == "ok"
    # 400 negotiation + bad attempt + good attempt; only the last two count as tries
    assert len(calls) == 3
    assert "temperature" not in calls[1]
    assert "temperature" not in calls[2]


@pytest.mark.asyncio
async def test_openai_falls_back_to_max_tokens_for_compat_servers():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if "max_completion_tokens" in calls[-1]:
            return httpx.Response(
                400,
                json={"error": {"message": "unknown field: max_completion_tokens"}},
            )
        return httpx.Response(200, json=_completion('{"label": "ok", "confidence": 0.5}'))

    client = OpenAIClient(model="local-test", transport=_transport(handler))
    out = await client.structured(system="s", user="u", schema=Verdict, max_tokens=99)
    assert out.label == "ok"
    assert calls[-1]["max_tokens"] == 99


@pytest.mark.asyncio
async def test_openai_keyless_compat_server_sends_no_auth_header(monkeypatch):
    _use_provider(monkeypatch, "openai", OPENAI_API_KEY="", OPENAI_BASE_URL="http://local/v1")
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json=_completion('{"label": "ok", "confidence": 0.5}'))

    client = OpenAIClient(transport=_transport(handler))
    await client.structured(system="s", user="u", schema=Verdict)
    assert "authorization" not in seen_headers[0]
    # base_url trailing-slash normalization feeds the request path
    assert str(seen_headers[0].get("host")) == "local"


# ---------------------------------------------------------------- get_llm dispatch


def test_get_llm_dispatches_openai(monkeypatch):
    _use_provider(monkeypatch, "openai", OPENAI_API_KEY="sk-test")
    assert isinstance(get_llm(), OpenAIClient)


def test_get_llm_anthropic_without_key_is_a_config_error(monkeypatch):
    _use_provider(monkeypatch, "anthropic", ANTHROPIC_API_KEY="")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is empty"):
        get_llm()


def test_get_llm_unknown_provider_raises(monkeypatch):
    _use_provider(monkeypatch, "chatgpt")  # the provider name is "openai"
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm()
