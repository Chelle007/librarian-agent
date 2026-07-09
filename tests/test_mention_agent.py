"""Agent tests for mention confirm + wikilink on create/update."""

from __future__ import annotations

import json

from librarian.agent import LibrarianAgent
from tests.test_agent import agent_with, make_llm


def test_create_gates_on_other_mentions(lib):
    lib.create(
        type="note",
        slug="friends-list",
        body="People to call:\nAngeline\nDesmond",
        raw_text="x",
    )
    agent = agent_with(
        lib,
        {
            "intent": "create",
            "note_type": "contact",
            "fields": {"name": "Angeline", "relationship": "bestie"},
            "body": "my bestie is Angeline",
        },
    )
    res = agent.handle("my bestie is Angeline")
    assert res.status == "needs_clarification"
    assert "mentioned elsewhere" in res.message
    assert "friends-list" in res.message


def test_create_with_mention_confirm_recovers_from_context(lib):
    """Bare 'yes' after mention gate must not create a junk note."""
    lib.create(
        type="note",
        slug="friends-list",
        body="Call:\nAngeline",
        raw_text="x",
    )
    context = (
        'I found "Angeline" mentioned elsewhere in your vault:\n'
        '- friends-list (note) — "Call: Angeline"\n'
        '(You said: "my bestie is Angeline")\n'
        "Same entity? Confirm to save and link these notes."
    )
    agent = agent_with(
        lib,
        {"intent": "create", "body": "yes", "is_confirmation": True},
        pending_sufficient=True,
        pending_note_type="contact",
        pending_fields={"name": "Angeline", "relationship": "bestie"},
        pending_body="my bestie is Angeline",
    )
    res = agent.handle("yes", context=context)
    assert res.status == "done" and res.action == "created"
    assert "contacts" in res.note_id
    note = lib.vault.read(res.note_id)
    assert note.frontmatter.get("name") == "Angeline"
    assert "friends-list" in note.frontmatter.get("links", [])


def test_create_with_mention_confirm_links(lib):
    lib.create(
        type="note",
        slug="friends-list",
        body="Call:\nAngeline",
        raw_text="x",
    )
    context = (
        'I found "Angeline" mentioned elsewhere in your vault:\n'
        "- friends-list (note) — \"Call: Angeline\"\n"
        "Same entity? Confirm to save and link these notes."
    )
    agent = agent_with(
        lib,
        {
            "intent": "create",
            "note_type": "contact",
            "fields": {"name": "Angeline", "relationship": "bestie"},
            "is_confirmation": True,
        },
    )
    res = agent.handle("yes", context=context)
    assert res.status == "done" and res.action == "created"
    note = lib.vault.read(res.note_id)
    assert "friends-list" in note.frontmatter.get("links", [])


def test_update_merges_links(lib):
    contact = lib.create(type="contact", fields={"name": "Angeline"}, body="Best friend.")
    lib.create(type="note", slug="gift-ideas", body="Angeline likes gelato", raw_text="x")
    agent = agent_with(
        lib,
        {
            "intent": "update",
            "target_ref": contact.path,
            "fields": {"name": "Angeline", "likes": "gelato"},
            "is_confirmation": True,
        },
    )
    context = (
        'I found "Angeline" mentioned elsewhere in your vault:\n'
        "- gift-ideas (note) — \"Angeline likes gelato\"\n"
        "Same entity? Confirm to update and link these notes."
    )
    res = agent.handle("yes", context=context)
    assert res.status == "done" and res.action == "updated"
    note = lib.vault.read(contact.path)
    assert "gift-ideas" in note.frontmatter.get("links", [])
