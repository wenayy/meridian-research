"""Configuration from environment variables (prefix ``MERIDIAN_``) or a .env file."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_", env_file=".env", extra="ignore")

    # LLMs: any OpenAI-compatible endpoint. A cheaper model can do the high-volume extraction.
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"          # planner, reviewer, writer, verifier
    fast_model: str | None = None            # extraction (defaults to llm_model)

    # search
    search_provider: Literal["tavily", "brave", "serper", "duckduckgo", "local"] = "duckduckgo"
    search_api_key: str | None = None
    local_corpus: Path = Path("benchmark/corpus.jsonl")
    cache_path: Path = Path(".meridian_cache.sqlite")

    # research depth & budgets
    min_sub_questions: int = 3
    max_sub_questions: int = 5
    results_per_query: int = 5
    sources_per_sub_question: int = 4
    min_credibility: float = 0.3            # below this a source is only used if nothing better exists
    max_rounds: int = 2                      # 1 = no gap-filling round
    max_gaps_per_round: int = 3
    max_workers: int = 6
    max_llm_calls: int = 120
    max_cost_usd: float = 1.00
    max_seconds: float = 600

    trace_file: Path | None = Path(".meridian_traces.jsonl")
    reports_dir: Path = Path("reports")


def get_settings() -> Settings:
    return Settings()
