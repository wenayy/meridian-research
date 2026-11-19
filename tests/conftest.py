from pathlib import Path

import pytest

from meridian.config import Settings
from meridian.llm import FakeLLM
from meridian.orchestrator import build_orchestrator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def settings(tmp_path):
    return Settings(search_provider="local", local_corpus=ROOT / "benchmark" / "corpus.jsonl",
                    cache_path=tmp_path / "cache.sqlite", trace_file=None, max_workers=4)


@pytest.fixture()
def make_orch(settings):
    def _make(handler=None, **overrides):
        s = settings.model_copy(update=overrides)
        return build_orchestrator(s, llm=FakeLLM(handler))
    return _make
