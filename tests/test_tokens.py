"""Tests for token accounting (TokenTracker + MeteredLLMClient)."""

from __future__ import annotations

from librarian.benchmark.tokens import (
    MeteredLLMClient,
    TokenTracker,
    approx_tokens,
    infer_phase,
)
from librarian.llm.gemini_client import FakeLLMClient


def test_approx_tokens():
    assert approx_tokens("") == 0
    assert approx_tokens("a" * 40) == 10


def test_infer_phase():
    assert infer_phase("Classify the request below", False) == "classify"
    assert infer_phase("check this", True) == "groundedness"
    assert infer_phase("answer this", False) == "generation"


def test_tracker_accumulates_and_buckets():
    t = TokenTracker()
    t.record("classify", prompt_tokens=100, output_tokens=10)
    t.record("generation", prompt_tokens=50, output_tokens=30)
    t.record("classify", prompt_tokens=20, output_tokens=5)
    assert t.calls == 3
    assert t.total_tokens == 215
    assert t.by_phase() == {"classify": 135, "generation": 80}


def test_tracker_reset():
    t = TokenTracker()
    t.record("classify", prompt_tokens=1, output_tokens=1)
    t.reset()
    assert t.calls == 0 and t.total_tokens == 0


def test_metered_client_estimates_when_no_usage():
    t = TokenTracker()
    m = MeteredLLMClient(FakeLLMClient(responses=["abcdefgh"]), t)  # 8 chars → 2 tokens out
    m.generate("Classify the request X", response_json=True)
    assert t.calls == 1
    ev = t.events[0]
    assert ev.phase == "classify"
    assert ev.output_tokens == 2


def test_metered_client_prefers_real_usage():
    class _UsageClient:
        last_usage = {"prompt": 100, "output": 20, "total": 120}

        def generate(self, prompt, *, system=None, response_json=False):
            return "ignored for counting"

    t = TokenTracker()
    MeteredLLMClient(_UsageClient(), t).generate("some generation prompt")
    ev = t.events[0]
    assert ev.prompt_tokens == 100
    assert ev.output_tokens == 20
