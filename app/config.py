from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://sr:sr@localhost:55432/standing_register"
    )

    # fixture | gemini | record
    model_backend: str = os.getenv("MODEL_BACKEND", "fixture")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    model_fast: str = os.getenv("MODEL_FAST", "gemini-flash-latest")
    model_deep: str = os.getenv("MODEL_DEEP", "gemini-pro-latest")

    # Ordered fallbacks, tried when the chosen model is unreachable. Hosted models
    # return 503 under load and retire without warning; a run that could have
    # finished on the next model along should not die because the first was busy.
    model_fallbacks_raw: str = os.getenv(
        "MODEL_FALLBACKS", "gemini-3.6-flash,gemini-flash-lite-latest"
    )

    @property
    def model_fallbacks(self) -> list[str]:
        return [m.strip() for m in self.model_fallbacks_raw.split(",") if m.strip()]

    # hashing | gemini
    embed_backend: str = os.getenv("EMBED_BACKEND", "hashing")
    embed_dim: int = 384

    corpus_dir: Path = Path(os.getenv("CORPUS_DIR", str(ROOT / "corpus")))
    rules_dir: Path = Path(os.getenv("RULES_DIR", str(ROOT / "rules")))
    watch_dir: Path = Path(os.getenv("WATCH_DIR", str(ROOT / "watched")))
    cassette_dir: Path = Path(os.getenv("CASSETTE_DIR", str(ROOT / "tests" / "cassettes")))
    fixture_file: Path = Path(
        os.getenv("FIXTURE_FILE", str(ROOT / "tests" / "fixtures" / "model_outputs.json"))
    )

    # Free-tier discipline: a run that would exceed this fails loudly rather than
    # silently truncating its own work. See README "Cost and the free tier".
    max_llm_calls_per_run: int = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "400"))
    llm_concurrency: int = int(os.getenv("LLM_CONCURRENCY", "4"))

    # Confidence floor below which classification is retried at the deep tier.
    classify_confidence_floor: float = 0.75

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# USD per 1M tokens. Rough public list prices; the point of behaviour 10 is that a
# run can account for itself, not that the third decimal place is authoritative.
# Aliases rather than pinned versions: a pinned model retires and the repo stops
# working for whoever clones it next. gemini-2.5-flash is already unavailable to
# new accounts, which is exactly how this list came to be checked.
PRICE_TABLE = {
    "gemini-flash-latest": {"in": 0.30, "out": 2.50},
    "gemini-pro-latest": {"in": 1.25, "out": 10.00},
    "gemini-3.6-flash": {"in": 0.30, "out": 2.50},
    "gemini-3.7-flash": {"in": 0.30, "out": 2.50},
    "gemini-3.5-flash": {"in": 0.30, "out": 2.50},
    "gemini-flash-lite-latest": {"in": 0.10, "out": 0.40},
    "gemini-3.1-flash-lite": {"in": 0.10, "out": 0.40},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.00},
    "fixture": {"in": 0.0, "out": 0.0},
}


def price(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICE_TABLE.get(model, PRICE_TABLE["fixture"])
    return round(tokens_in / 1_000_000 * p["in"] + tokens_out / 1_000_000 * p["out"], 6)
