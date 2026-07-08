"""Tests for the auto-generated retrieval eval harness."""

from __future__ import annotations

import pytest

from librarian.eval.harness import (
    EvalCase,
    KeywordQuestionGenerator,
    build_eval_set,
    score,
)
from librarian.retrieval.semantic import SemanticRetriever


def test_keyword_generator_excludes_title_words():
    gen = KeywordQuestionGenerator()
    q = gen.generate(
        "notes/x.md",
        {"type": "note", "name": "Sourdough"},
        "sourdough baking with flour and yeast fermentation",
    )
    assert "sourdough" not in q  # title word excluded
    assert "fermentation" in q  # a salient body word kept


def test_build_eval_set_one_case_per_note(lib):
    lib.create(type="note", body="python decorators generators asyncio", raw_text="x")
    lib.create(type="note", body="telescope galaxies nebula cosmology", raw_text="x")
    cases = build_eval_set(lib.vault, KeywordQuestionGenerator())
    assert len(cases) == 2
    assert all(isinstance(c, EvalCase) and c.question for c in cases)


def test_score_finds_gold_notes(lib):
    lib.create(type="note", body="python decorators generators asyncio concurrency", raw_text="x")
    lib.create(type="note", body="sourdough bread baking flour yeast fermentation", raw_text="x")
    lib.create(type="note", body="telescope galaxies nebula stars cosmology redshift", raw_text="x")

    cases = build_eval_set(lib.vault, KeywordQuestionGenerator())
    retriever = SemanticRetriever(lib.vector_store, lib.embedder)
    report = score(retriever, cases, k=3)

    # distinct topics + keyword questions drawn from each note -> gold should rank top-1
    assert report.total == 3
    assert report.recall_at_k == 1.0
    assert report.mrr == pytest.approx(1.0)


def test_score_reports_misses():
    class NullRetriever:
        def search(self, query, k):
            return []

    cases = [EvalCase(note_path="notes/a.md", question="q")]
    report = score(NullRetriever(), cases, k=5)
    assert report.hits_at_k == 0
    assert report.recall_at_k == 0.0
    assert report.per_case[0]["rank"] is None


def test_empty_eval_set_scores_zero():
    class NullRetriever:
        def search(self, query, k):
            return []

    report = score(NullRetriever(), [], k=5)
    assert report.total == 0
    assert report.recall_at_k == 0.0
    assert "recall@5" in report.summary()
