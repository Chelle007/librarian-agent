"""Tests for context-to-path matching."""

from __future__ import annotations

from librarian.llm.context_gate import context_references_path


def test_context_references_path():
    assert context_references_path("update 👤 contacts/desmond.md", "👤 contacts/desmond.md")
    assert context_references_path("from contacts/desmond.md", "👤 contacts/desmond.md")
    assert not context_references_path(None, "👤 contacts/desmond.md")
