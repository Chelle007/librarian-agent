"""Tests for the hybrid path (metadata pre-filter + vector search within the set)."""

from __future__ import annotations

import pytest

from librarian.retrieval.hybrid import HybridRetriever
from librarian.vault_folders import NOTES_FOLDER, TASKS_FOLDER


@pytest.fixture
def hybrid(lib):
    # two topically-similar notes of different types; hybrid must respect the type filter
    lib.create(type="note", body="sourdough bread baking flour yeast fermentation", raw_text="x")
    lib.create(type="task", fields={"due_date": "2026-01-01"}, body="buy flour and yeast for bread")
    lib.create(type="note", body="telescope galaxies nebula stars cosmology", raw_text="x")
    return HybridRetriever(lib.vector_store, lib.embedder, lib.meta)


def test_type_filter_restricts_candidates(hybrid):
    hits = hybrid.search("bread yeast flour", type="task", k=5)
    assert hits
    assert all(TASKS_FOLDER in h.note_path for h in hits)


def test_without_filter_ranks_globally(hybrid):
    hits = hybrid.search("bread yeast flour baking", k=5)
    assert hits[0].note_path.startswith(f"{NOTES_FOLDER}/")


def test_date_range_filter(lib):
    lib.create(type="note", fields={"created_date": "2020-01-01"}, body="old astronomy stars")
    lib.create(type="note", fields={"created_date": "2026-06-01"}, body="new astronomy stars")
    h = HybridRetriever(lib.vector_store, lib.embedder, lib.meta)

    hits = h.search("astronomy stars", created_after="2026-01-01", k=5)
    assert len(hits) == 1
    assert lib.vault.read(hits[0].note_path).frontmatter["created_date"] == "2026-06-01"


def test_empty_query_returns_nothing(hybrid):
    assert hybrid.search("   ", type="note", k=3) == []


def test_no_candidates_returns_nothing(hybrid):
    assert hybrid.search("anything", type="habit", k=3) == []  # no habits exist


def test_dim_mismatch_rejected(lib):
    from librarian.llm.embeddings import HashingEmbedder

    with pytest.raises(ValueError):
        HybridRetriever(lib.vector_store, HashingEmbedder(dim=lib.vector_store.dim + 1), lib.meta)
