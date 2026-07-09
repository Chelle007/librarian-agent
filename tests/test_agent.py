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


def make_llm(
    classification: dict,
    *,
    answer: str = "",
    grounded: bool = True,
    revised: str = "",
    conflict: bool = False,
    conflict_message: str = "",
):
    """A fake LLM that dispatches by prompt/flag to the right scripted reply."""
    payload = {"actionable": True, **classification}
    classify_json = json.dumps(payload)
    ground_json = json.dumps({"grounded": grounded, "revised": revised})
    conflict_json = json.dumps({"conflict": conflict, "message": conflict_message})

    def handler(prompt, system, response_json):
        if "Classify the request" in prompt:
            return classify_json
        if response_json and "contradict" in prompt.lower():
            return conflict_json
        if response_json:
            return ground_json
        return answer

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


def test_handle_update_conflict_asks_confirmation(lib):
    created = lib.create(
        type="contact",
        fields={"name": "Desmond", "relationship": "boyfriend"},
        body="Desmond is my boyfriend.",
    )
    agent = agent_with(
        lib,
        {
            "intent": "update",
            "target_ref": "Desmond",
            "fields": {"relationship": "girlfriend"},
            "body": "desmond is my girlfriend",
        },
        conflict=True,
        conflict_message="The note says Desmond is your boyfriend, but you said girlfriend.",
    )
    res = agent.handle("desmond is my girlfriend")
    assert res.status == "needs_clarification"
    assert res.pending_id
    assert "boyfriend" in res.message.lower()
    assert lib.vault.read(created.path).frontmatter["relationship"] == "boyfriend"

    confirmed = agent.handle_confirm(res.pending_id, approved=True)
    assert confirmed.status == "done" and confirmed.action == "updated"
    assert lib.vault.read(created.path).frontmatter["relationship"] == "girlfriend"
    assert len(lib.meta.get_corrections()) == 1


def test_handle_update_without_reaction_does_not_log(lib):
    created = lib.create(type="contact", fields={"name": "Alex"})
    agent = agent_with(
        lib, {"intent": "update", "target_ref": created.path, "fields": {"likes": "tea"}}
    )
    res = agent.handle("Alex likes tea")
    assert res.status == "done" and res.action == "updated"
    assert len(lib.meta.get_corrections()) == 0


def test_expired_pending_needs_clarification(lib):
    created = lib.create(
        type="contact",
        fields={"name": "Desmond", "relationship": "boyfriend"},
        body="Desmond is my boyfriend.",
    )
    agent = agent_with(
        lib,
        {
            "intent": "update",
            "target_ref": "Desmond",
            "fields": {"relationship": "girlfriend"},
            "body": "desmond is my girlfriend",
        },
        conflict=True,
        conflict_message="Conflicts with boyfriend.",
    )
    gate = agent.handle("desmond is my girlfriend")
    lib.meta.settle_pending(gate.pending_id, "expired")
    res = agent.handle_confirm(gate.pending_id, approved=True)
    assert res.status == "needs_clarification"
    assert lib.vault.read(created.path).frontmatter["relationship"] == "boyfriend"


# ------------------------------------------------------------------- delete
def test_handle_delete_confirms_then_deletes(lib):
    created = lib.create(type="note", body="junk to remove", raw_text="x")
    classification = {"intent": "delete", "target_ref": created.path}
    agent = agent_with(lib, classification)

    # first pass: must ask for confirmation, note not yet gone
    first = agent.handle("delete that junk note")
    assert first.status == "needs_clarification"
    assert first.pending_id
    assert "junk" in first.message.lower() or created.path in (first.note_id or "")
    assert lib.meta.get(created.path) is not None

    second = agent.handle("", pending_id=first.pending_id, approved=True)
    assert second.status == "done" and second.action == "deleted"
    assert lib.meta.get(created.path) is None


def test_handle_pending_id_delegates_to_confirm(lib):
    created = lib.create(type="note", body="junk", raw_text="x")
    agent = agent_with(lib, {"intent": "delete", "target_ref": created.path})
    gate = agent.handle("delete it")
    res = agent.handle("yes", pending_id=gate.pending_id, approved=True)
    assert res.status == "done" and res.action == "deleted"


def test_handle_pending_id_without_approved_errors(lib):
    agent = agent_with(lib, {"intent": "create", "fields": {"name": "X"}})
    res = agent.handle("yes", pending_id="deadbeef1234", approved=None)
    assert res.status == "error"


# ------------------------------------------------------------- correction
def test_correction_updates_vault_and_logs(lib):
    created = lib.create(
        type="contact",
        fields={"name": "Desmond", "relationship": "girlfriend"},
        body="She is my friend.",
    )
    agent = agent_with(
        lib,
        {
            "intent": "update",
            "is_reaction": True,
            "target_ref": created.path,
            "fields": {"relationship": "friend"},
            "body": "He is a guy, not a girl.",
        },
    )
    res = agent.handle("nonono desmond is a guy, not a girl")
    assert res.status == "done" and res.action == "updated"
    assert len(lib.meta.get_corrections()) == 1
    assert lib.meta.get_corrections()[0]["note_id"] == created.path
    note = lib.vault.read(created.path)
    assert note.frontmatter.get("relationship") == "friend"


def test_factual_correction_updates_and_logs(lib):
    created = lib.create(type="contact", fields={"name": "Desmond"}, body="Friend group.")
    agent = agent_with(
        lib,
        {
            "intent": "update",
            "target_ref": "Desmond",
            "fields": {"relationship": "boyfriend"},
            "is_reaction": True,
        },
    )
    res = agent.handle(
        "desmond is my bf actually",
        context="User asked who Desmond is. Librarian answered from contacts/desmond.md",
    )
    assert res.status == "done" and res.action == "updated"
    assert len(lib.meta.get_corrections()) == 1
    note = lib.vault.read(created.path)
    assert note.frontmatter.get("relationship") == "boyfriend"


def test_create_existing_contact_redirects_to_update(lib):
    created = lib.create(type="contact", fields={"name": "Desmond"}, body="Friend group.")
    agent = agent_with(
        lib,
        {
            "intent": "create",
            "note_type": "contact",
            "fields": {"name": "Desmond", "relationship": "boyfriend"},
            "body": "desmond is my bf",
        },
    )
    res = agent.handle("desmond is my bf")
    assert res.status == "done" and res.action == "updated"
    assert res.note_id == created.path


def test_bare_reply_without_context_needs_clarification(lib):
    agent = agent_with(
        lib,
        {
            "intent": "create",
            "actionable": False,
            "clarify_message": "What would you like me to do?",
        },
    )
    res = agent.handle("yes")
    assert res.status == "needs_clarification"
    assert res.action is None


def test_ambiguous_target_suggests_best_guess(lib):
    lib.create(type="contact", fields={"name": "Alex"}, slug="alex-work", body="Work friend.")
    work = lib.meta.query(type="contact")[0]["path"]
    lib.meta.upsert(path=work, type="contact", last_modified="2026-01-01")
    lib.create(type="contact", fields={"name": "Alex"}, slug="alex-school", body="School friend.")
    school = next(
        p["path"] for p in lib.meta.query(type="contact") if p["path"].endswith("alex-school.md")
    )
    lib.meta.upsert(path=school, type="contact", last_modified="2026-06-01")

    agent = agent_with(
        lib,
        {"intent": "update", "fields": {"name": "Alex"}, "body": "Alex likes tea"},
    )
    res = agent.handle("Alex likes tea")
    assert res.status == "needs_clarification"
    assert "Alex" in res.message
    assert "Confirm" in res.message
    assert res.note_id == school


# -------------------------------------------------------------- vectors off
def test_semantic_query_errors_when_vectors_disabled(temp_vault):
    from librarian.pipeline import Librarian
    from librarian.vault_folders import SYSTEM_FOLDER

    lib = Librarian(
        vault_root=temp_vault,
        db_path=":memory:",
        schema_path=temp_vault / SYSTEM_FOLDER / "schema.json",
        vector_enabled=False,
    )
    try:
        agent = agent_with(lib, {"intent": "query", "mode": "semantic", "query_text": "x"})
        res = agent.handle("find something")
        assert res.status == "error"
    finally:
        lib.close()
