"""Tests for note -> embed-text composition."""

from __future__ import annotations

from librarian.ingestion.embed_text import note_embed_text


def test_freeform_note_uses_body():
    text = note_embed_text({"type": "note", "created_date": "2024-01-01"}, "a thought about cats")
    assert text == "a thought about cats"


def test_title_leads_and_bookkeeping_fields_dropped():
    fm = {
        "type": "contact",
        "created_date": "2024-01-01",
        "last_modified": "2024-01-02",
        "name": "Alex Kim",
        "likes": ["coffee", "hiking"],
        "birthday": "2000-05-01",
    }
    text = note_embed_text(fm, "met at a conference")

    assert text.splitlines()[0] == "Alex Kim"  # title/name leads
    assert "likes: coffee, hiking" in text  # list flattened
    assert "birthday: 2000-05-01" in text
    assert "met at a conference" in text  # body included
    assert "type:" not in text and "created_date" not in text  # bookkeeping dropped


def test_empty_note_is_empty_string():
    assert note_embed_text({"type": "note"}, "") == ""


def test_nested_dict_field_skipped():
    text = note_embed_text({"type": "note", "socials": {"x": "@a"}}, "body")
    assert "socials" not in text
    assert "body" in text
