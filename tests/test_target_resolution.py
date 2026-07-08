"""Tests for update/delete target resolution."""

from __future__ import annotations

from dataclasses import dataclass

from librarian.retrieval.semantic import SemanticRetriever
from librarian.target_resolution import resolve_target


@dataclass
class _Hit:
    note_path: str
    score: float


class _StubRetriever:
    """Returns a fixed hit list, ignoring the query — for deterministic tests."""

    def __init__(self, hits):
        self._hits = hits

    def search(self, query, k=5):
        return self._hits[:k]


def test_explicit_existing_path_resolves(lib):
    res = lib.create(type="note", body="something", raw_text="x")
    tr = resolve_target(res.path, meta=lib.meta, retriever=_StubRetriever([]), context=None)
    assert tr.resolved
    assert tr.path == res.path


def test_single_strong_candidate_resolves(lib):
    lib.create(type="note", body="python asyncio concurrency", raw_text="x")
    lib.create(type="note", body="sourdough bread baking", raw_text="x")
    retriever = SemanticRetriever(lib.vector_store, lib.embedder)
    tr = resolve_target("asyncio concurrency", meta=lib.meta, retriever=retriever)
    assert tr.resolved
    assert "python" in tr.path or tr.path.startswith("notes/")
    assert tr.confidence == 1.0


def test_ambiguous_cluster_not_resolved(lib):
    # two near-identical notes -> tiny margin -> both candidates -> ambiguous
    a = _Hit("notes/a.md", 0.90)
    b = _Hit("notes/b.md", 0.89)
    lib.meta.upsert(path="notes/a.md", type="note", last_modified="2026-01-01")
    lib.meta.upsert(path="notes/b.md", type="note", last_modified="2026-02-01")
    tr = resolve_target("thing", meta=lib.meta, retriever=_StubRetriever([a, b]))
    assert not tr.resolved
    assert set(tr.candidates) == {"notes/a.md", "notes/b.md"}
    # recency tie-break points the best guess at the newer note
    assert tr.path == "notes/b.md"


def test_no_hits_unresolved(lib):
    tr = resolve_target("nothing", meta=lib.meta, retriever=_StubRetriever([]))
    assert not tr.resolved
    assert tr.path is None


def test_empty_ref_and_context_unresolved(lib):
    tr = resolve_target("", meta=lib.meta, retriever=_StubRetriever([_Hit("notes/a.md", 0.9)]))
    assert not tr.resolved
