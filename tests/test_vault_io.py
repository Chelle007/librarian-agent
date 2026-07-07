"""Tests for direct vault file I/O (librarian/store/vault_io.py)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from librarian.store.vault_io import VaultIO, VaultIOError


@pytest.fixture
def vault(tmp_path):
    return VaultIO(vault_root=tmp_path)


def test_write_then_read_roundtrip(vault):
    path = vault.write(
        frontmatter={"type": "note", "created_date": "2026-07-07"},
        body="Hello world",
        folder="notes/",
    )
    assert path.exists()
    note = vault.read(path)
    assert note.frontmatter["type"] == "note"
    assert note.body.strip() == "Hello world"


def test_filename_from_name(vault):
    path = vault.write(
        frontmatter={"type": "contact", "name": "Alex Tan"},
        folder="contacts/",
    )
    assert path.name == "alex-tan.md"


def test_write_is_non_clobbering(vault):
    fm = {"type": "contact", "name": "Alex"}
    p1 = vault.write(frontmatter=fm, folder="contacts/")
    p2 = vault.write(frontmatter=fm, folder="contacts/")
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_write_creates_missing_folder(vault):
    path = vault.write(frontmatter={"type": "task"}, folder="tasks/", slug="thing")
    assert path.parent.name == "tasks"
    assert path.exists()


def test_archive_raw_is_append_only(vault):
    when = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    r1 = vault.archive_raw("first input", when=when)
    r2 = vault.archive_raw("second input", when=when)
    assert r1 != r2  # same timestamp must not overwrite
    assert "2026-07-07" in str(r1)
    assert vault.read(r1).body.strip() == "first input"
    assert vault.read(r2).body.strip() == "second input"


def test_soft_delete_moves_to_trash(vault):
    path = vault.write(frontmatter={"type": "note"}, folder="notes/", slug="junk")
    dest = vault.soft_delete(path)
    assert not path.exists()
    assert dest.exists()
    assert ".trash" in dest.parts
    assert dest.parts[-2] == "notes"  # relative path preserved


def test_soft_delete_missing_raises(vault):
    with pytest.raises(VaultIOError):
        vault.soft_delete("notes/does-not-exist.md")


def test_overwrite_updates_in_place(vault):
    path = vault.write(frontmatter={"type": "note"}, folder="notes/", slug="x", body="v1")
    same = vault.overwrite(path, frontmatter={"type": "note"}, body="v2")
    assert same == path
    assert vault.read(path).body.strip() == "v2"


def test_path_escape_is_blocked(vault):
    with pytest.raises(VaultIOError):
        vault.read("../../etc/passwd")
