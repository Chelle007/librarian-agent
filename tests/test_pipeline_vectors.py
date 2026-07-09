"""Tests for vector indexing wired into the write pipeline.

create/update/delete keep the vector store in lockstep with the vault, and
reindex rebuilds it from source-of-truth markdown.
"""

from __future__ import annotations

from librarian.llm.embeddings import HashingEmbedder
from librarian.pipeline import Librarian
from librarian.retrieval.semantic import SemanticRetriever


def test_create_indexes_vectors(lib):
    res = lib.create(type="note", body="cats are independent feline pets", raw_text="x")
    assert lib.vector_store.count_notes() == 1
    hits = SemanticRetriever(lib.vector_store, lib.embedder).search("feline cats", k=3)
    assert hits and hits[0].note_path == res.path


def test_contact_with_no_body_still_indexed(lib):
    # embed text falls back to name + fields; a contact is never empty
    res = lib.create(type="contact", fields={"name": "Alex", "likes": "coffee"})
    assert lib.vector_store.count_notes() == 1
    hits = SemanticRetriever(lib.vector_store, lib.embedder).search("Alex coffee", k=1)
    assert hits[0].note_path == res.path


def test_update_reembeds_without_stale_chunks(lib):
    res = lib.create(type="note", body="original gardening content", raw_text="x")
    lib.update(res.path, body="totally different cooking content")
    assert lib.vector_store.count_notes() == 1
    assert lib.vector_store.count_chunks() == 1  # no stale chunk left behind

    hits = SemanticRetriever(lib.vector_store, lib.embedder).search("cooking", k=1)
    assert hits[0].note_path == res.path


def test_delete_removes_vectors(lib):
    res = lib.create(type="note", body="something to remove", raw_text="x")
    assert lib.vector_store.count_notes() == 1
    lib.delete(res.path)
    assert lib.vector_store.count_notes() == 0


def test_reindex_rebuilds_vectors(lib):
    lib.create(type="note", body="alpha content", raw_text="x")
    lib.create(type="note", body="beta content", raw_text="x")
    assert lib.vector_store.count_notes() == 2

    # reindex should clear + rebuild to exactly match the vault
    assert lib.reindex() == 2
    assert lib.vector_store.count_notes() == 2


def test_vectors_can_be_disabled(temp_vault):
    from librarian.vault_folders import SYSTEM_FOLDER

    lib = Librarian(
        vault_root=temp_vault,
        db_path=":memory:",
        schema_path=temp_vault / SYSTEM_FOLDER / "schema.json",
        vector_enabled=False,
        embedder=HashingEmbedder(dim=64),  # ignored when disabled
    )
    try:
        assert lib.vector_store is None
        res = lib.create(type="note", body="still works", raw_text="x")
        assert res.ok  # pipeline unaffected by vectors being off
    finally:
        lib.close()
