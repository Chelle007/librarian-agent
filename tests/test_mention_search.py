"""Tests for vault mention search and wikilink merging."""

from __future__ import annotations

from librarian.classifier import Classification
from librarian.link_resolution import apply_links, merge_links
from librarian.mention_search import find_mentions, identity_label


def test_find_mentions_word_boundary(lib):
    lib.create(type="note", slug="email-note", body="angelinesryd22@gmail.com", raw_text="x")
    lib.create(
        type="note",
        slug="friends-list",
        body="Call:\nAngeline\nDesmond",
        raw_text="x",
    )
    hits = find_mentions("Angeline", lib.meta, lib.vault)
    paths = {h.path for h in hits}
    assert any("friends-list" in p for p in paths)
    assert not any("email-note" in p for p in paths)


def test_merge_links_dedupes():
    assert merge_links(["a"], ["b", "a"]) == ["a", "b"]


def test_apply_links_merges_existing():
    out = apply_links(
        {"relationship": "bestie"},
        existing={"links": ["old-note"]},
        paths=["notes/friends-list.md"],
        extra=["classifier-link"],
    )
    assert "old-note" in out["links"]
    assert "friends-list" in out["links"]
    assert "classifier-link" in out["links"]


def test_identity_label_prefers_name():
    assert identity_label({"name": "Angeline", "relationship": "bestie"}) == "Angeline"
