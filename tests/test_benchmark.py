"""Tests for the token A/B benchmark harness (offline, via FakeLLMClient)."""

from __future__ import annotations

import json

from librarian.benchmark.ab import (
    BenchRequest,
    run_arm_a,
    run_arm_b,
    run_benchmark,
    seed_demo_vault,
)
from librarian.llm.gemini_client import FakeLLMClient

REQUESTS = [
    BenchRequest("c1", "idea: batch embed calls", "create"),
    BenchRequest("q1", "how many contacts do I have?", "exact_lookup"),
    BenchRequest("q2", "what did I note about embed calls?", "semantic"),
]


def _arm_a_llm():
    """Dispatch classification + generation + groundedness by request content."""
    def handler(prompt, system, response_json):
        if "Classify the request" in prompt:
            # match request-specific phrases (avoid words that appear in the
            # classifier's own instruction template, e.g. "how many")
            low = prompt.lower()
            if "contacts do i have" in low:
                return json.dumps(
                    {"intent": "query", "mode": "exact_lookup",
                     "filters": {"type": "contact", "aggregate": True}}
                )
            if "what did i note about" in low:
                return json.dumps({"intent": "query", "mode": "semantic", "query_text": "embed calls"})
            return json.dumps({"intent": "create", "note_type": "note", "body": "batch embed calls"})
        if response_json:
            return json.dumps({"grounded": True, "revised": ""})
        return "You noted batching embed calls."

    return FakeLLMClient(handler=handler)


def test_arm_a_structured_query_spends_no_generation_tokens(lib):
    lib.create(type="contact", fields={"name": "Sam"})
    results = {r.request_id: r for r in run_arm_a(lib, _arm_a_llm(), REQUESTS)}

    # exact_lookup answers from SQLite: exactly one LLM call (classification), no generation
    q1 = results["q1"]
    assert q1.calls == 1
    assert "classify" in q1.by_phase
    assert "generation" not in q1.by_phase


def test_arm_a_semantic_query_spends_generation_tokens(lib):
    lib.create(type="note", body="batch the embed calls to cut latency", raw_text="x")
    results = {r.request_id: r for r in run_arm_a(lib, _arm_a_llm(), REQUESTS)}

    q2 = results["q2"]
    assert q2.calls >= 2  # classify + generation (+ groundedness)
    assert "generation" in q2.by_phase


def test_arm_a_create_is_single_call(lib):
    results = {r.request_id: r for r in run_arm_a(lib, _arm_a_llm(), REQUESTS)}
    c1 = results["c1"]
    assert c1.status == "done" and c1.action == "created"
    assert c1.calls == 1  # classification only; the write itself is LLM-free


def test_arm_b_single_incontext_call(lib):
    seed_demo_vault(lib)
    llm = FakeLLMClient(handler=lambda p, s, j: "an answer")
    results = run_arm_b(lib, llm, REQUESTS)
    assert len(results) == 3
    for r in results:
        assert r.arm == "B"
        assert r.calls == 1  # one big in-context call
        assert r.total_tokens > 0  # vault dump is non-empty


def test_run_benchmark_report(lib):
    seed_demo_vault(lib)
    report = run_benchmark(lib, _arm_a_llm(), requests=REQUESTS)
    totals = report.totals_by_arm()
    assert totals["A"] > 0 and totals["B"] > 0
    text = report.format_text()
    assert "TOTAL Arm A" in text
    assert "Avg tokens by kind" in text
