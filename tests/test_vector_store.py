"""Tests for the sqlite-vec vector store (chunk-native, note-granularity search)."""

from __future__ import annotations

import pytest

from librarian.ingestion.chunker import Chunk
from librarian.llm.embeddings import HashingEmbedder
from librarian.store.vector_store import VectorStore

DIM = 64


@pytest.fixture
def store():
    vs = VectorStore(db_path=":memory:", dim=DIM)
    yield vs
    vs.close()


@pytest.fixture
def embedder():
    return HashingEmbedder(dim=DIM)


def _index(store, embedder, note_path, note_type, text):
    from librarian.ingestion.chunker import chunk_note

    chunks = chunk_note(note_type, text)
    embs = embedder.embed_documents([c.text for c in chunks])
    return store.index_note(note_path, chunks, embs)


def test_index_and_count(store, embedder):
    _index(store, embedder, "notes/a.md", "note", "cats are wonderful pets")
    assert store.count_notes() == 1
    assert store.count_chunks() == 1


def test_search_returns_closest_note(store, embedder):
    _index(store, embedder, "notes/cats.md", "note", "cats are independent feline pets")
    _index(store, embedder, "notes/cars.md", "note", "engines pistons turbo horsepower torque")

    hits = SemanticShim(store, embedder).search("feline cats pets", k=2)
    assert hits[0].note_path == "notes/cats.md"


def test_reindex_replaces_chunks_no_stale(store, embedder):
    _index(store, embedder, "notes/x.md", "note", "original content about gardening")
    assert store.count_chunks() == 1
    # re-index same note with new content — must not accumulate stale chunks
    _index(store, embedder, "notes/x.md", "note", "brand new content about cooking")
    assert store.count_notes() == 1
    assert store.count_chunks() == 1


def test_delete_note_removes_vectors(store, embedder):
    _index(store, embedder, "notes/x.md", "note", "something to delete")
    store.delete_note("notes/x.md")
    assert store.count_notes() == 0
    assert store.count_chunks() == 0


def test_multi_chunk_note_collapses_to_one_hit(store, embedder):
    chunks = [Chunk(0, "chapter one about volcanoes"), Chunk(1, "chapter two about volcanoes")]
    embs = embedder.embed_documents([c.text for c in chunks])
    store.index_note("notes/long.md", chunks, embs)
    assert store.count_chunks() == 2

    hits = store.search(embedder.embed_query("volcanoes"), k=5)
    paths = [h.note_path for h in hits]
    assert paths.count("notes/long.md") == 1  # collapsed, not duplicated


def test_dim_mismatch_rejected(store):
    with pytest.raises(ValueError):
        store.search([0.0] * (DIM + 1), k=1)
    with pytest.raises(ValueError):
        store.index_note("notes/x.md", [Chunk(0, "t")], [[0.0] * (DIM - 1)])


def test_length_mismatch_rejected(store, embedder):
    with pytest.raises(ValueError):
        store.index_note("notes/x.md", [Chunk(0, "a"), Chunk(1, "b")], [embedder.embed_query("a")])


class SemanticShim:
    """Local helper so this file doesn't depend on the retrieval module."""

    def __init__(self, store, embedder):
        self.store, self.embedder = store, embedder

    def search(self, q, k=5):
        return self.store.search(self.embedder.embed_query(q), k=k)
