"""Tests for the semantic retrieval path (embedder + vector store wired together)."""

from __future__ import annotations

import pytest

from librarian.ingestion.chunker import chunk_note
from librarian.llm.embeddings import HashingEmbedder
from librarian.retrieval.semantic import SemanticRetriever
from librarian.store.vector_store import VectorStore

DIM = 128


@pytest.fixture
def retriever():
    vs = VectorStore(db_path=":memory:", dim=DIM)
    embedder = HashingEmbedder(dim=DIM)
    # seed a tiny corpus
    corpus = {
        "notes/python.md": "python programming language decorators generators asyncio",
        "notes/baking.md": "sourdough bread baking flour yeast fermentation oven",
        "notes/astronomy.md": "telescope galaxies nebula stars cosmology redshift",
    }
    for path, text in corpus.items():
        chunks = chunk_note("note", text)
        embs = embedder.embed_documents([c.text for c in chunks])
        vs.index_note(path, chunks, embs)
    r = SemanticRetriever(vs, embedder)
    yield r
    vs.close()


def test_retrieves_topically_matching_note(retriever):
    hits = retriever.search("how do python generators and asyncio work", k=3)
    assert hits
    assert hits[0].note_path == "notes/python.md"


def test_k_limits_results(retriever):
    assert len(retriever.search("stars galaxies", k=1)) == 1


def test_empty_query_returns_nothing(retriever):
    assert retriever.search("   ", k=3) == []


def test_hit_exposes_grounding_text_and_score(retriever):
    hit = retriever.search("sourdough bread yeast", k=1)[0]
    assert hit.note_path == "notes/baking.md"
    assert "sourdough" in hit.text
    assert -1.0 <= hit.score <= 1.0


def test_dim_mismatch_between_store_and_embedder_rejected():
    vs = VectorStore(db_path=":memory:", dim=64)
    try:
        with pytest.raises(ValueError):
            SemanticRetriever(vs, HashingEmbedder(dim=128))
    finally:
        vs.close()
