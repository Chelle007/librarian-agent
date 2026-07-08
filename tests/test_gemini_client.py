"""Tests for the LLM transport layer (parse_json + FakeLLMClient)."""

from __future__ import annotations

import pytest

from librarian.llm.gemini_client import FakeLLMClient, parse_json


def test_parse_plain_json():
    assert parse_json('{"intent": "create"}') == {"intent": "create"}


def test_parse_strips_code_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_grabs_json_amid_prose():
    assert parse_json('Sure! {"ok": true} hope that helps') == {"ok": True}


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse_json("   ")


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        parse_json("not json at all")


def test_fake_client_queue_pops_in_order():
    c = FakeLLMClient(responses=["a", "b"])
    assert c.generate("p1") == "a"
    assert c.generate("p2") == "b"
    assert c.generate("p3") == ""  # exhausted → empty
    assert len(c.calls) == 3


def test_fake_client_handler_sees_flags():
    def handler(prompt, system, response_json):
        return "json" if response_json else "text"

    c = FakeLLMClient(handler=handler)
    assert c.generate("p", response_json=True) == "json"
    assert c.generate("p") == "text"
    assert c.calls[0]["response_json"] is True
