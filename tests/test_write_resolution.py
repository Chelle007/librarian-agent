"""Tests for create-vs-update write target resolution."""

from __future__ import annotations

from dataclasses import dataclass

from librarian.classifier import Classification
from librarian.target_resolution import resolve_existing_path
from librarian.write_resolution import infer_note_type, resolve_write_target


def test_resolve_existing_path_suffix_match(lib):
    created = lib.create(type="contact", fields={"name": "Desmond"}, body="Friend.")
    assert resolve_existing_path("contacts/desmond.md", lib.meta) == created.path


def test_identity_match_prefers_canonical_slug(lib):
    lib.create(type="contact", fields={"name": "Desmond"}, body="Friend.")
    lib.create(type="contact", fields={"name": "Desmond"}, slug="Desmond", body="Dup.")

    c = Classification(intent="create", note_type="note", fields={}, body="desmond is my bf")
    wr = resolve_write_target(
        c,
        "desmond is my bf",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=None,
    )
    assert wr.action == "update"
    assert wr.path.endswith("desmond.md")
    assert not wr.path.endswith("desmond-1.md")


def test_context_path_resolves_emoji_folder(lib):
    created = lib.create(type="contact", fields={"name": "Desmond"}, body="Friend.")
    c = Classification(intent="update", is_reaction=True)
    wr = resolve_write_target(
        c,
        "he is my boyfriend",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=None,
        context=f"Librarian answered from contacts/desmond.md",
    )
    assert wr.action == "update"
    assert wr.path == created.path


def test_no_match_creates(lib):
    c = Classification(intent="create", note_type="note", body="brand new idea about rust")
    wr = resolve_write_target(
        c,
        "brand new idea about rust",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=None,
    )
    assert wr.action == "create"


def test_unknown_name_creates_contact(lib):
    c = Classification(
        intent="create",
        note_type="contact",
        fields={"name": "Angeline", "relationship": "bestie"},
        body="my bestie is Angeline",
    )
    wr = resolve_write_target(
        c,
        "my bestie is Angeline",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=_StubRetriever([_Hit("notes/friends.md", 0.9)]),
    )
    assert wr.action == "create"


def test_semantic_skipped_for_unknown_named_person(lib):
    c = Classification(
        intent="update",
        fields={"name": "Angeline", "relationship": "bestie"},
        body="my bestie is Angeline",
    )
    wr = resolve_write_target(
        c,
        "my bestie is Angeline",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=_StubRetriever([_Hit("notes/hello.md", 0.99)]),
    )
    assert wr.action == "create"
    assert wr.path is None


def test_infer_note_type_from_name():
    from librarian.store.schema import Schema

    schema = Schema.load()
    assert infer_note_type(schema, {"name": "Angeline"}) == "contact"


def test_infer_note_type_prefers_full_habit_match():
    from librarian.store.schema import Schema

    schema = Schema.load()
    assert (
        infer_note_type(schema, {"name": "Meditate", "frequency": "daily"}) == "habit"
    )
    assert infer_note_type(schema, {"name": "Angeline"}, note_type_hint="contact") == "contact"


def test_unknown_habit_creates_not_semantic(lib):
    c = Classification(
        intent="update",
        note_type="habit",
        fields={"name": "Stretch", "frequency": "daily"},
        body="stretch daily habit",
    )
    wr = resolve_write_target(
        c,
        "stretch daily habit",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=_StubRetriever([_Hit("notes/hello.md", 0.99)]),
    )
    assert wr.action == "create"


@dataclass
class _Hit:
    note_path: str
    score: float


class _StubRetriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, k=5):
        return self._hits[:k]


def test_ambiguous_identity_clarify_picks_primary(lib):
    lib.create(type="contact", fields={"name": "Alex"}, slug="alex-work", body="Work friend.")
    lib.meta.upsert(path=lib.meta.query(type="contact")[0]["path"], type="contact", last_modified="2026-01-01")
    lib.create(type="contact", fields={"name": "Alex"}, slug="alex-school", body="School friend.")
    paths = [r["path"] for r in lib.meta.query(type="contact")]
    school = next(p for p in paths if p.endswith("alex-school.md"))
    lib.meta.upsert(path=school, type="contact", last_modified="2026-06-01")

    c = Classification(intent="update", fields={"name": "Alex"}, body="Alex likes tea")
    wr = resolve_write_target(
        c,
        "Alex likes tea",
        schema=lib.schema,
        meta=lib.meta,
        vault=lib.vault,
        retriever=None,
    )
    assert wr.action == "clarify"
    assert wr.path == school
