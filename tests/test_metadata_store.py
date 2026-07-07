"""Tests for the SQLite metadata store (librarian/store/metadata_store.py)."""

from __future__ import annotations

import pytest

from librarian.store.metadata_store import MetadataStore


@pytest.fixture
def store():
    s = MetadataStore(db_path=":memory:")
    yield s
    s.close()


def test_upsert_and_get(store):
    store.upsert(
        path="notes/idea.md",
        type="note",
        tags=["ml", "ideas"],
        created_date="2026-07-07",
    )
    row = store.get("notes/idea.md")
    assert row is not None
    assert row["type"] == "note"
    assert row["tags"] == ["ml", "ideas"]
    assert row["last_modified"]  # auto-filled


def test_upsert_is_idempotent_on_path(store):
    store.upsert(path="notes/x.md", type="note", created_date="2026-01-01")
    store.upsert(path="notes/x.md", type="note", tags=["updated"], created_date="2026-01-02")
    assert store.get("notes/x.md")["tags"] == ["updated"]
    assert store.get("notes/x.md")["created_date"] == "2026-01-02"
    assert store.count() == 1


def test_query_by_type(store):
    store.upsert(path="notes/a.md", type="note", created_date="2026-01-01")
    store.upsert(path="contacts/b.md", type="contact", created_date="2026-01-02")
    results = store.query(type="contact")
    assert len(results) == 1
    assert results[0]["path"] == "contacts/b.md"


def test_query_by_tag(store):
    store.upsert(path="notes/a.md", type="note", tags=["book"], created_date="2026-01-01")
    store.upsert(path="notes/b.md", type="note", tags=["game"], created_date="2026-01-02")
    results = store.query(tag="book")
    assert [r["path"] for r in results] == ["notes/a.md"]


def test_query_date_range_and_order(store):
    store.upsert(path="notes/a.md", type="note", created_date="2026-01-01")
    store.upsert(path="notes/b.md", type="note", created_date="2026-02-01")
    store.upsert(path="notes/c.md", type="note", created_date="2026-03-01")
    results = store.query(created_after="2026-01-15", created_before="2026-02-15")
    assert [r["path"] for r in results] == ["notes/b.md"]

    asc = store.query(descending=False)
    assert [r["path"] for r in asc] == ["notes/a.md", "notes/b.md", "notes/c.md"]


def test_query_limit(store):
    for i in range(5):
        store.upsert(path=f"notes/{i}.md", type="note", created_date=f"2026-01-0{i+1}")
    assert len(store.query(limit=2)) == 2


def test_invalid_order_by_raises(store):
    with pytest.raises(ValueError):
        store.query(order_by="; DROP TABLE notes")


def test_delete(store):
    store.upsert(path="notes/gone.md", type="note", created_date="2026-01-01")
    store.delete("notes/gone.md")
    assert store.get("notes/gone.md") is None


def test_rename(store):
    store.upsert(path="notes/old.md", type="note", created_date="2026-01-01")
    store.rename("notes/old.md", "notes/new.md")
    assert store.get("notes/old.md") is None
    assert store.get("notes/new.md") is not None


def test_count(store):
    store.upsert(path="notes/a.md", type="note", tags=["book"], created_date="2026-01-01")
    store.upsert(path="notes/b.md", type="note", tags=["book"], created_date="2026-01-02")
    store.upsert(path="contacts/c.md", type="contact", created_date="2026-01-03")
    assert store.count() == 3
    assert store.count(type="note") == 2
    assert store.count(tag="book") == 2


def test_log_and_get_corrections(store):
    store.log_correction(
        note_id="notes/a.md",
        original_classification="task",
        corrected_to="note",
    )
    store.log_correction(
        note_id="notes/b.md",
        original_classification="note",
        corrected_to="contact",
    )
    all_c = store.get_corrections()
    assert len(all_c) == 2
    one = store.get_corrections(note_id="notes/a.md")
    assert len(one) == 1
    assert one[0]["corrected_to"] == "note"
    assert one[0]["timestamp"]


def test_context_manager():
    with MetadataStore(db_path=":memory:") as s:
        s.upsert(path="notes/a.md", type="note", created_date="2026-01-01")
        assert s.get("notes/a.md") is not None
