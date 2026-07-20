"""Embedding providers. Voyage for quality; a deterministic hashing embedder for $0/offline mode.

Both produce 1024-dim unit vectors to match the pgvector schema.
"""

import hashlib
import math
import re
from typing import Protocol

import httpx

from craftsman.core.config import get_settings

DIM = 1024


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class VoyageEmbedder:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.voyage_api_key
        self.model = model or settings.voyage_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model, "output_dimension": DIM},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    """Deterministic bag-of-ngrams hashing embedder. Not semantically deep, but free,
    offline, and consistent — similar texts share tokens, so cosine similarity is
    meaningful enough for coarse ICP filtering and for tests."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        tokens = _TOKEN_RE.findall(text.lower())
        grams = tokens + [" ".join(p) for p in zip(tokens, tokens[1:])]
        for g in grams:
            h = int.from_bytes(hashlib.md5(g.encode()).digest()[:8], "big")
            idx = h % DIM
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def get_embedder() -> Embedder:
    if get_settings().embedding_provider == "voyage":
        return VoyageEmbedder()
    return HashEmbedder()
