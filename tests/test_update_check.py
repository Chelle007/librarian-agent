"""Tests for update conflict detection."""

from __future__ import annotations

import json

from librarian.llm.gemini_client import FakeLLMClient
from librarian.llm.update_check import check_update_conflict


def test_no_conflict_when_llm_says_ok():
    llm = FakeLLMClient(responses=[json.dumps({"conflict": False, "message": ""})])
    res = check_update_conflict(
        llm,
        existing_frontmatter={"relationship": "boyfriend"},
        existing_body="Desmond is my boyfriend.",
        proposed_fields={"relationship": "boyfriend"},
        proposed_body="Desmond is my boyfriend.",
        request="desmond is my bf",
    )
    assert not res.has_conflict


def test_conflict_returns_message():
    llm = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "conflict": True,
                    "message": "The note says boyfriend but you said girlfriend.",
                }
            )
        ]
    )
    res = check_update_conflict(
        llm,
        existing_frontmatter={"relationship": "boyfriend"},
        existing_body="Desmond is my boyfriend.",
        proposed_fields={"relationship": "girlfriend"},
        proposed_body="Desmond is my girlfriend.",
        request="desmond is my girlfriend",
    )
    assert res.has_conflict
    assert "boyfriend" in res.message


def test_parse_error_fails_open():
    llm = FakeLLMClient(responses=["not json"])
    res = check_update_conflict(
        llm,
        existing_frontmatter={},
        existing_body="",
        proposed_fields={"relationship": "girlfriend"},
        proposed_body=None,
        request="desmond is my girlfriend",
    )
    assert not res.has_conflict
