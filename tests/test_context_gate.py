"""Tests for the context/actionability gate."""

from __future__ import annotations

import json

from librarian.llm.context_gate import assess_context, context_references_path
from librarian.llm.gemini_client import FakeLLMClient


def test_assess_not_actionable_without_context():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "actionable": False,
                    "message": "What would you like me to confirm?",
                    "is_confirmation": False,
                }
            )
        ]
    )
    res = assess_context(llm, request="yes", context=None, intent="create")
    assert not res.actionable
    assert "confirm" in res.message.lower()


def test_assess_confirmation_with_context():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "actionable": True,
                    "message": "",
                    "is_confirmation": True,
                }
            )
        ]
    )
    res = assess_context(
        llm,
        request="sure",
        context="Delete notes/junk.md? confirm to proceed.",
        intent="delete",
        target_ref="notes/junk.md",
    )
    assert res.actionable
    assert res.is_confirmation


def test_assess_parse_error_fails_open():
    llm = FakeLLMClient(responses=["not json"])
    res = assess_context(llm, request="hello", context=None, intent="create")
    assert res.actionable


def test_context_references_path():
    assert context_references_path("update 👤 contacts/desmond.md", "👤 contacts/desmond.md")
    assert context_references_path("from contacts/desmond.md", "👤 contacts/desmond.md")
    assert not context_references_path(None, "👤 contacts/desmond.md")
