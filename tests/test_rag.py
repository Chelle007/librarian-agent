"""Tests for RAG answer generation + groundedness verification."""

from __future__ import annotations

import json
from dataclasses import dataclass

from librarian.llm.gemini_client import FakeLLMClient
from librarian.llm.rag import check_groundedness, generate_answer


@dataclass
class _Hit:
    note_path: str
    text: str


def test_generate_answer_no_hits_short_circuits():
    llm = FakeLLMClient(responses=["should not be used"])
    out = generate_answer(llm, "anything", [])
    assert "couldn't find" in out.lower()
    assert llm.calls == []  # no LLM call when there's nothing to ground on


def test_generate_answer_uses_sources():
    llm = FakeLLMClient(responses=["You watched Dune."])
    hits = [_Hit("notes/dune.md", "Watched Dune, rated 5 stars")]
    out = generate_answer(llm, "what did I watch?", hits)
    assert out == "You watched Dune."
    # the retrieved chunk text is passed into the prompt
    assert "Dune, rated 5 stars" in llm.calls[0]["prompt"]


def test_groundedness_pass_keeps_answer():
    llm = FakeLLMClient(responses=[json.dumps({"grounded": True, "revised": ""})])
    hits = [_Hit("notes/a.md", "source text")]
    res = check_groundedness(llm, "an answer", hits)
    assert res.grounded
    assert res.answer == "an answer"


def test_groundedness_fail_uses_revision():
    llm = FakeLLMClient(responses=[json.dumps({"grounded": False, "revised": "corrected answer"})])
    hits = [_Hit("notes/a.md", "source text")]
    res = check_groundedness(llm, "hallucinated answer", hits)
    assert not res.grounded
    assert res.answer == "corrected answer"


def test_groundedness_fail_empty_revision_hedges():
    llm = FakeLLMClient(responses=[json.dumps({"grounded": False, "revised": ""})])
    hits = [_Hit("notes/a.md", "source text")]
    res = check_groundedness(llm, "hallucinated", hits)
    assert not res.grounded
    assert "enough" in res.answer.lower()


def test_groundedness_parse_error_fails_open():
    llm = FakeLLMClient(responses=["not json"])
    hits = [_Hit("notes/a.md", "source text")]
    res = check_groundedness(llm, "an answer", hits)
    assert res.grounded  # fail open: keep the original rather than block
    assert res.answer == "an answer"
