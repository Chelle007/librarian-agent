"""Agent tests for mention confirm + wikilink on create/update."""

from __future__ import annotations

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
    assert res.pending_id
    assert "mentioned elsewhere" in res.message
    assert "friends-list" in res.message


def test_create_with_pending_confirm_links(lib):
    lib.create(
        type="note",
        slug="friends-list",
        body="Call:\nAngeline",
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
    gate = agent.handle("my bestie is Angeline")
    assert gate.pending_id

    res = agent.handle_confirm(gate.pending_id, approved=True)
    assert res.status == "done" and res.action == "created"
    note = lib.vault.read(res.note_id)
    assert note.frontmatter.get("name") == "Angeline"
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
        },
    )
    gate = agent.handle("Angeline likes gelato")
    assert gate.pending_id

    res = agent.handle_confirm(gate.pending_id, approved=True)
    assert res.status == "done" and res.action == "updated"
    note = lib.vault.read(contact.path)
    assert "gift-ideas" in note.frontmatter.get("links", [])


def test_reject_pending_cancels(lib):
    lib.create(type="note", slug="friends-list", body="Angeline", raw_text="x")
    agent = agent_with(
        lib,
        {
            "intent": "create",
            "note_type": "contact",
            "fields": {"name": "Angeline"},
        },
    )
    gate = agent.handle("Angeline is my friend")
    res = agent.handle_confirm(gate.pending_id, approved=False)
    assert res.status == "done" and res.message == "Cancelled."
    assert lib.meta.get_pending(gate.pending_id) is None
