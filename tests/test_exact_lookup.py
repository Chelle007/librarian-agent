"""Tests for the exact_lookup path (structured + keyword + aggregation)."""

from __future__ import annotations

import pytest

from librarian.retrieval.exact_lookup import ExactLookup
from librarian.vault_folders import CONTACTS_FOLDER, NOTES_FOLDER


@pytest.fixture
def lookup(lib):
    lib.create(type="contact", fields={"name": "Alex", "birthday": "2000-05-01"})
    lib.create(type="contact", fields={"name": "Sam", "likes": "coffee"})
    lib.create(type="task", fields={"due_date": "2026-07-10", "status": "open"}, body="file taxes")
    lib.create(type="note", body="sourdough baking notes", raw_text="x")
    # unknown type -> notes/ fallback, tagged with the intended type
    lib.create(type="book", body="Dune by Frank Herbert", raw_text="x")
    return ExactLookup(lib.meta, lib.vault)


def test_structured_type_filter(lookup):
    res = lookup.lookup(type="contact")
    assert res.count == 2
    assert all(h.type == "contact" for h in res.hits)


def test_single_contact_renders_detail(lookup):
    res = lookup.lookup(type="contact", keyword="alex")
    assert res.count == 1
    assert "Alex" in res.message
    assert "birthday 2000-05-01" in res.message


def test_keyword_matches_title_slug(lookup):
    # keyword matches the note's title/slug (a contact's name becomes its filename)
    res = lookup.lookup(keyword="sam")
    assert res.count == 1
    assert res.hits[0].path == f"{CONTACTS_FOLDER}/sam.md"


def test_keyword_matches_tag(lookup):
    # 'book' is only present as a tag on the fallback-bucket note, not in any slug
    res = lookup.lookup(keyword="book")
    assert res.count == 1
    assert res.hits[0].path.startswith(f"{NOTES_FOLDER}/")
    assert "book" in res.hits[0].tags


def test_keyword_no_match(lookup):
    res = lookup.lookup(keyword="nonexistentword")
    assert res.count == 0
    assert "No notes found" in res.message


def test_limit_applies_after_keyword(lookup):
    res = lookup.lookup(type="contact", limit=1)
    assert res.count == 1


def test_aggregation_dual_check_flags_undercount(lookup):
    # 'book' isn't a formal type -> the Dune note is a `note` tagged 'book'
    res = lookup.lookup(type="book", aggregate=True)
    assert res.aggregation is not None
    assert res.aggregation.strict_count == 0  # nothing is *typed* book
    assert res.aggregation.tag_count == 1  # one note tagged book
    assert res.aggregation.discrepancy == 1
    assert "tagged 'book'" in res.message


def test_aggregation_formal_type_no_discrepancy(lookup):
    res = lookup.lookup(type="contact", aggregate=True)
    assert res.aggregation.strict_count == 2
    assert res.aggregation.discrepancy == 0
    assert "Found 2 contacts by type." == res.message


def test_multiple_results_rendered_as_list(lookup):
    res = lookup.lookup(type="contact")
    assert res.message.startswith("Found 2 matches:")
    assert res.message.count("- ") == 2
