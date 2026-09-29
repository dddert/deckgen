"""E2E с моделью (сервер с vLLM): DECKGEN_E2E_LLM=1 pytest tests/e2e/test_llm_pipeline.py"""
import os
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DECKGEN_E2E_LLM"), reason="нужен инференс: DECKGEN_E2E_LLM=1")


def test_llm_run_under_budget(templates, pack, monkeypatch):
    from deckgen.pipeline.orchestrator import Pipeline
    from deckgen.settings import Settings
    s = Settings.load()
    p = Pipeline(s)
    assert p.llm is not None, "модель недоступна"
    t0 = time.perf_counter()
    res = p.run(templates[0], pack)
    assert time.perf_counter() - t0 < s.run.max_generation_seconds
    assert res.mode == "llm"
    assert all(v.plan.llm_mode in ("llm", "mixed") for v in res.variants.values())
