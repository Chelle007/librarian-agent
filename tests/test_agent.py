"""End-to-end tests for the LibrarianAgent handle() orchestrator.

A single dispatching FakeLLMClient stands in for Gemini: it returns a scripted
classification for the classifier prompt, a scripted answer for RAG generation,
and a scripted verdict for the groundedness check — so the whole route runs
deterministically offline.
"""

from __future__ import annotations

import json

import pytest

from librarian.agent import LibrarianAgent
from librarian.llm.gemini_client import FakeLLMClient


def make_llm(classification: dict, *, answer: str = "", grounded: bool = True, revised: str = ""):
    """A fake LLM that dispatches by prompt/flag to the right scripted reply."""
    classify_json = json.dumps(classification)
    ground_json = json.dumps({"grounded": grounded, "revised": revised})

    def handler(prompt, system, response_json):
        if "Classify the request" in prompt:
            return classify_json
        if response_json:  # the groundedness fact-check call
            return ground_json
        return answer  # RAG generation (free text)

    return FakeLLMClient(handler=handler)


def agent_with(lib, classification, **kw):
    return LibrarianAgent(lib, make_llm(classification, **kw))


# ------------------------------------------------------------------- create
def test_handle_create_contact(lib):
    agent = agent_with(
        lib, {"intent": "create", "note_type": "contact", "fields": {"name": "Alex"}, "tags": ["friend"]}
    )
    res = agent.handle("save Alex as a friend")
    assert res.status == "done" and res.action == "created"
    assert res.note_id.startswith("👤 contacts/")
    assert lib.vault.read(res.note_id).frontmatter["name"] == "Alex"


def test_handle_create_missing_required_asks(lib):
    agent = agent_with(lib, {"intent": "create", "note_type": "contact", "fields": {}})
    res = agent.handle("save a contact")
    assert res.status == "needs_clarification"
    assert "name" in res.message


# -------------------------------------------------------------------- query
def test_handle_query_exact_aggregate(lib):
    lib.create(type="contact", fields={"name": "Alex"})
    lib.create(type="contact", fields={"name": "Sam"})
    agent = agent_with(
        lib, {"intent": "query", "mode": "exact_lookup", "filters": {"type": "contact", "aggregate": True}}
    )
    res = agent.handle("how many contacts do I have?")
    assert res.status == "done" and res.action == "queried"
    assert res.message == "Found 2 contacts by type."


def test_handle_query_semantic_rag(lib):
    lib.create(type="note", body="Watched Dune, rated 5 stars", raw_text="x")
    agent = agent_with(
        lib,
        {"intent": "query", "mode": "semantic", "query_text": "what did I watch"},
        answer="You watched Dune and rated it 5 stars.",
        grounded=True,
    )
    res = agent.handle("what movie did I watch")
    assert res.status == "done" and res.action == "queried"
    assert res.message == "You watched Dune and rated it 5 stars."


def test_handle_query_semantic_ungrounded_is_rewritten(lib):
    lib.create(type="note", body="Watched Dune", raw_text="x")
    agent = agent_with(
        lib,
        {"intent": "query", "mode": "semantic", "query_text": "rating"},
        answer="You rated Dune 5 stars.",  # not supported by the note
        grounded=False,
        revised="Your note says you watched Dune, but doesn't record a rating.",
    )
    res = agent.handle("what did I rate Dune")
    assert res.status == "done"
    assert "doesn't record a rating" in res.message


def test_handle_query_semantic_no_results(lib):
    agent = agent_with(lib, {"intent": "query", "mode": "semantic", "query_text": "unicorns"})
    res = agent.handle("anything about unicorns?")
    assert res.status == "done"
    assert "couldn't find" in res.message.lower()


# ------------------------------------------------------------------- update
def test_handle_update_explicit_target(lib):
    created = lib.create(type="contact", fields={"name": "Alex"})
    agent = agent_with(
        lib, {"intent": "update", "target_ref": created.path, "fields": {"likes": "coffee"}}
    )
    res = agent.handle("Alex likes coffee")
    assert res.status == "done" and res.action == "updated"
    assert lib.vault.read(created.path).frontmatter["likes"] == "coffee"


# ------------------------------------------------------------------- delete
def test_handle_delete_confirms_then_deletes(lib):
    created = lib.create(type="note", body="junk to remove", raw_text="x")
    classification = {"intent": "delete", "target_ref": created.path}
    agent = agent_with(lib, classification)

    # first pass: must ask for confirmation, note not yet gone
    first = agent.handle("delete that junk note")
    assert first.status == "needs_clarification"
    assert created.path in first.message
    assert lib.meta.get(created.path) is not None

    # second pass: affirmative confirms the soft-delete
    second = agent.handle("yes", context=f"delete {created.path}")
    assert second.status == "done" and second.action == "deleted"
    assert lib.meta.get(created.path) is None


# ----------------------------------------------------------------- reaction
def test_handle_reaction_logs_correction(lib):
    agent = agent_with(
        lib, {"intent": "update", "is_reaction": True, "target_ref": "notes/x.md"}
    )
    res = agent.handle("correct the librarian: that was a task, not a note")
    assert res.status == "done"
    assert len(lib.meta.get_corrections()) == 1


# -------------------------------------------------------------- vectors off
def test_semantic_query_errors_when_vectors_disabled(temp_vault):
    from librarian.pipeline import Librarian

    lib = Librarian(
        vault_root=temp_vault,
        db_path=":memory:",
        schema_path=temp_vault / "system" / "schema.json",
        vector_enabled=False,
    )
    try:
        agent = agent_with(lib, {"intent": "query", "mode": "semantic", "query_text": "x"})
        res = agent.handle("find something")
        assert res.status == "error"
    finally:
        lib.close()
