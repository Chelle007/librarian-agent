"""Tests for note preview formatting."""

from __future__ import annotations

from librarian.note_preview import format_note_preview, format_target_confirm


def test_format_note_preview_with_name_and_body(lib):
    created = lib.create(
        type="contact",
        fields={"name": "Angeline"},
        body="Friend from church, loves hiking.",
    )
    preview = format_note_preview(lib.vault, created.path)
    assert "Angeline" in preview
    assert "church" in preview


def test_format_target_confirm_update(lib):
    created = lib.create(type="contact", fields={"name": "Angeline"}, body="Best friend.")
    msg = format_target_confirm(lib.vault, created.path, action="update")
    assert "Angeline" in msg
    assert "Confirm" in msg
    assert "different" in msg.lower()
