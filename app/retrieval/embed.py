from __future__ import annotations

import hashlib
import math
import re

import httpx

from app.config import settings

_TOKEN = re.compile(r"[a-z0-9]+")


def embed(text: str) -> list[float]:
    if settings.embed_backend == "gemini" and settings.gemini_api_key:
        try:
            return _gemini_embed(text)
        except Exception:  # noqa: BLE001
            # Graceful degradation: a dense-retrieval outage must not take the run
            # down, because lexical retrieval alone still returns citable passages.
            return _hashing_embed(text)
    return _hashing_embed(text)


def embed_many(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


def _hashing_embed(text: str) -> list[float]:
    """Dependency-free, offline, deterministic hashing embedder.

    A deliberate trade, documented in the README: this keeps the image small, the
    test suite keyless and every run reproducible, at the cost of semantic recall.
    Hybrid search leans on Postgres full-text for precision -- which is the right
    bias for contract work, where clause numbers, defined terms and exact amounts
    matter more than paraphrase.
    """
    dim = settings.embed_dim
    vec = [0.0] * dim
    words = _TOKEN.findall(text.lower())
    grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for g in grams:
        h = hashlib.blake2b(g.encode(), digest_size=8).digest()
        i = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[i] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def _gemini_embed(text: str) -> list[float]:
    r = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent",
        params={"key": settings.gemini_api_key},
        json={
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": text[:8000]}]},
            "outputDimensionality": settings.embed_dim,
        },
        timeout=30.0,
    )
    r.raise_for_status()
    values = r.json()["embedding"]["values"]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]
