"""Tests for pending update recovery from context."""

from __future__ import annotations

import json

from librarian.classifier import Classification
from librarian.llm.gemini_client import FakeLLMClient
from librarian.llm.pending_update import (
    has_proposed_changes,
    is_pending_mutation_context,
    recover_pending_mutation,
    recover_pending_update,
)


def test_has_proposed_changes_fields():
    c = Classification(intent="update", fields={"age": 21})
    assert has_proposed_changes(c, "yes")


def test_has_proposed_changes_echo_body_is_not_enough():
    c = Classification(intent="update", body="yes")
    assert not has_proposed_changes(c, "yes")


def test_recover_insufficient_when_context_only_names_note():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "sufficient": False,
                    "message": "What should I change on that note?",
                }
            )
        ]
    )
    res = recover_pending_update(
        llm,
        context="Confirm update contacts/desmond.md",
        path="contacts/desmond.md",
        existing_frontmatter={"name": "Desmond"},
        existing_body="Friend.",
        request="yes",
    )
    assert not res.sufficient
    assert "change" in res.message.lower()


def test_recover_extracts_pending_change():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "sufficient": True,
                    "fields": {"relationship": "girlfriend"},
                    "body": "desmond is my girlfriend",
                }
            )
        ]
    )
    res = recover_pending_update(
        llm,
        context="User: desmond is my girlfriend. Librarian: conflicts with boyfriend.",
        path="contacts/desmond.md",
        existing_frontmatter={"relationship": "boyfriend"},
        existing_body="Desmond is my boyfriend.",
        request="sure",
    )
    assert res.sufficient
    assert res.fields["relationship"] == "girlfriend"


def test_is_pending_mutation_context_mention_gate():
    assert is_pending_mutation_context('I found "Angeline" mentioned elsewhere')
    assert not is_pending_mutation_context("random chat")


def test_recover_pending_mutation_from_mention_context():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "sufficient": True,
                    "note_type": "contact",
                    "fields": {"name": "Angeline", "relationship": "bestie"},
                    "body": "my bestie is Angeline",
                }
            )
        ]
    )
    res = recover_pending_mutation(
        llm,
        context=(
            'I found "Angeline" mentioned elsewhere.\n'
            '(You said: "my bestie is Angeline")\n'
            "Same entity? Confirm to save and link these notes."
        ),
        request="yes",
    )
    assert res.sufficient
    assert res.note_type == "contact"
    assert res.fields["name"] == "Angeline"
