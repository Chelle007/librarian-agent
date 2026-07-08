"""Tests for the Stage 1 write pipeline (librarian/pipeline.py)."""

from __future__ import annotations

from datetime import date

import pytest

from librarian.store.vault_io import VaultIOError


def test_create_note(lib):
    res = lib.create(type="note", body="a thought", raw_text="a thought")
    assert res.ok
    assert res.action == "created"
    assert res.path.startswith("📝 notes/")
    # file exists and round-trips
    note = lib.vault.read(res.path)
    assert note.body.strip() == "a thought"
    assert note.frontmatter["type"] == "note"
    assert note.frontmatter["created_date"] == date.today().isoformat()
    # indexed
    assert lib.meta.get(res.path) is not None


def test_create_archives_raw(lib):
    lib.create(type="note", body="processed", raw_text="the ORIGINAL input")
    raw_files = list((lib.vault.root / ".raw").rglob("*.md"))
    assert len(raw_files) == 1
    assert "the ORIGINAL input" in raw_files[0].read_text(encoding="utf-8")


def test_create_missing_required_fails_no_write(lib):
    res = lib.create(type="contact")  # missing name
    assert not res.ok
    assert "name" in res.missing_required
    assert lib.meta.count() == 0
    assert not list((lib.vault.root / "contacts").glob("*.md"))


def test_create_unknown_type_falls_back_and_tags(lib):
    res = lib.create(type="recipe", body="pasta", raw_text="pasta")
    assert res.ok
    assert res.path.startswith("📝 notes/")
    note = lib.vault.read(res.path)
    assert note.frontmatter["type"] == "note"
    assert "recipe" in note.frontmatter["tags"]


def test_create_contact_and_habit(lib):
    c = lib.create(type="contact", fields={"name": "Alex"})
    assert c.ok and c.path.startswith("👤 contacts/")
    h = lib.create(type="habit", fields={"name": "Water", "frequency": "8h"})
    assert h.ok and h.path.startswith("🔁 habits/")


def test_update(lib):
    res = lib.create(type="contact", fields={"name": "Alex"}, body="v1")
    upd = lib.update(res.path, fields={"birthday": "2000-01-01"}, body="v2")
    assert upd.ok and upd.action == "updated"
    note = lib.vault.read(res.path)
    assert note.frontmatter["birthday"] == "2000-01-01"
    assert note.body.strip() == "v2"


def test_update_missing_note_raises(lib):
    with pytest.raises(VaultIOError):
        lib.update("notes/nope.md", fields={"x": 1})


def test_delete_is_soft(lib):
    res = lib.create(type="note", body="junk", raw_text="junk")
    dele = lib.delete(res.path)
    assert dele.ok and dele.action == "deleted"
    # gone from index and from notes/, but present in .trash/
    assert lib.meta.get(res.path) is None
    assert not (lib.vault.root / res.path).exists()
    assert dele.path.startswith(".trash/")
    assert (lib.vault.root / dele.path).exists()


def test_query_raw(lib):
    lib.create(type="contact", fields={"name": "Alex"})
    lib.create(type="contact", fields={"name": "Sam"})
    lib.create(type="note", body="x", raw_text="x")
    contacts = lib.query_raw(type="contact")
    assert len(contacts) == 2


def test_created_date_override(lib):
    res = lib.create(type="note", fields={"created_date": "2020-01-01"}, body="old")
    assert lib.vault.read(res.path).frontmatter["created_date"] == "2020-01-01"
    assert lib.meta.get(res.path)["created_date"] == "2020-01-01"
