"""Tests for the classifier: rule pre-filter, LLM parse, confidence heuristics."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from librarian.classifier import (
    CONFIDENCE_THRESHOLD,
    Classifier,
    confidence_from_candidates,
    confidence_from_margin,
    is_confident,
    rule_prefilter,
    vector_margin,
)
from librarian.llm.gemini_client import FakeLLMClient
from librarian.store.schema import Schema


@dataclass
class _Hit:
    score: float


@pytest.fixture
def schema():
    return Schema.load()  # packaged template


# --------------------------------------------------------------- pre-filter
def test_prefilter_fires_on_plain_statement():
    c = rule_prefilter("cleaned the garage today")
    assert c is not None and c.intent == "create"
    assert c.source == "prefilter"
    assert c.body == "cleaned the garage today"


def test_prefilter_defers_on_question():
    assert rule_prefilter("what did I do last week?") is None


def test_prefilter_defers_on_mutation_verb():
    assert rule_prefilter("delete the grocery note") is None
    assert rule_prefilter("update my address") is None


def test_prefilter_defers_on_reference_word():
    assert rule_prefilter("rate that 5 stars") is None  # 'that' + 'rate'


# ------------------------------------------------------------------ LLM path
def test_classify_parses_llm_json(schema):
    payload = {
        "intent": "query",
        "mode": "hybrid",
        "query_text": "sourdough article",
        "filters": {"type": "note", "created_after": "2026-06-01"},
    }
    llm = FakeLLMClient(responses=[json.dumps(payload)])
    c = Classifier(llm, schema).classify("that sourdough article from last month")
    assert c.intent == "query"
    assert c.mode == "hybrid"
    assert c.filters["created_after"] == "2026-06-01"


def test_classify_unknown_intent_falls_back_to_query(schema):
    # use_prefilter=False so the input reaches the LLM path under test
    llm = FakeLLMClient(responses=['{"intent": "banana"}'])
    c = Classifier(llm, schema, use_prefilter=False).classify("hmm")
    assert c.intent == "query"  # safest fallback: read, don't mutate


def test_classify_unparseable_is_ambiguous_query(schema):
    llm = FakeLLMClient(responses=["totally not json"])
    c = Classifier(llm, schema, use_prefilter=False).classify("weird input")
    assert c.intent == "query" and c.mode == "semantic"


def test_classify_prefilter_skips_llm(schema):
    llm = FakeLLMClient(responses=['{"intent": "delete"}'])  # should NOT be consumed
    c = Classifier(llm, schema, use_prefilter=True).classify("watered the plants")
    assert c.intent == "create"
    assert llm.calls == []  # LLM never called


# ---------------------------------------------------------------- confidence
def test_vector_margin_single_hit_is_max():
    assert vector_margin([_Hit(0.9)]) == 1.0


def test_vector_margin_gap():
    assert vector_margin([_Hit(0.9), _Hit(0.6)]) == pytest.approx(0.3)


def test_confidence_from_margin_saturates():
    assert confidence_from_margin([_Hit(0.9), _Hit(0.2)]) == 1.0  # big gap
    assert confidence_from_margin([_Hit(0.90), _Hit(0.89)]) < 0.2  # tiny gap
    assert confidence_from_margin([]) == 0.0


def test_confidence_from_candidates():
    assert confidence_from_candidates(1) == 1.0
    assert confidence_from_candidates(0) == 0.0
    assert confidence_from_candidates(3) == 0.0


def test_is_confident_threshold():
    assert is_confident(CONFIDENCE_THRESHOLD)
    assert not is_confident(CONFIDENCE_THRESHOLD - 0.01)
