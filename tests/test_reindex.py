"""Tests for the index rebuild (Librarian.reindex).

The metadata index is a derived cache; reindex rebuilds it from the vault
markdown (source of truth) so external edits (e.g. a git pull from desktop
Obsidian) are reconciled.
"""

from __future__ import annotations

import frontmatter


def _write_note(vault_root, rel_path, meta, body=""):
    """Write a markdown file straight to disk, bypassing the pipeline/index."""
    p = vault_root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(frontmatter.dumps(frontmatter.Post(body, **meta)) + "\n", encoding="utf-8")
    return p


def test_reindex_picks_up_external_files(lib):
    # a note that appeared on disk without going through the pipeline
    _write_note(
        lib.vault.root,
        "notes/from-desktop.md",
        {"type": "note", "created_date": "2024-05-01", "tags": ["imported"]},
        body="written in Obsidian",
    )
    assert lib.meta.get("notes/from-desktop.md") is None

    n = lib.reindex()
    assert n == 1
    row = lib.meta.get("notes/from-desktop.md")
    assert row is not None
    assert row["type"] == "note"
    assert row["created_date"] == "2024-05-01"
    assert row["tags"] == ["imported"]


def test_reindex_drops_stale_rows(lib):
    res = lib.create(type="note", body="junk", raw_text="junk")
    assert lib.meta.get(res.path) is not None
    # file removed externally (not via soft-delete), index left stale
    (lib.vault.root / res.path).unlink()

    lib.reindex()
    assert lib.meta.get(res.path) is None
    assert lib.meta.count() == 0


def test_reindex_matches_pipeline_state(lib):
    lib.create(type="contact", fields={"name": "Alex"})
    lib.create(type="contact", fields={"name": "Sam"})
    lib.create(type="note", body="x", raw_text="x")
    before = lib.meta.count()

    assert lib.reindex() == before
    assert lib.query_raw(type="contact")  # still queryable
    assert len(lib.query_raw(type="contact")) == 2


def test_reindex_skips_raw_and_trash(lib):
    # create then soft-delete: file now lives in .trash/, plus a .raw/ entry exists
    res = lib.create(type="note", body="junk", raw_text="junk")
    lib.delete(res.path)

    # a fresh live note so there's something legitimate to index
    lib.create(type="note", body="keeper", raw_text="keeper")

    n = lib.reindex()
    assert n == 1  # only the keeper — .raw/ and .trash/ excluded
    paths = [r["path"] for r in lib.query_raw()]
    assert all(not p.startswith(".") for p in paths)


def test_reindex_unknown_type_resolves_to_fallback(lib):
    _write_note(
        lib.vault.root,
        "notes/weird.md",
        {"type": "recipe", "created_date": "2024-01-01"},
    )
    lib.reindex()
    row = lib.meta.get("notes/weird.md")
    assert row["type"] == "note"  # unknown 'recipe' -> fallback


def test_reindex_preserves_corrections(lib):
    lib.meta.log_correction(
        note_id="notes/x.md", original_classification="note", corrected_to="task"
    )
    lib.create(type="note", body="x", raw_text="x")

    lib.reindex()
    assert len(lib.meta.get_corrections()) == 1  # corrections survive an index rebuild
