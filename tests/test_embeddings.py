"""Tests for the embedding helper (offline HashingEmbedder + normalization)."""

from __future__ import annotations

import math

from librarian.llm.embeddings import (
    EMBED_DIM,
    Embedder,
    HashingEmbedder,
    get_embedder,
    l2_normalize,
)


def test_l2_normalize_unit_length():
    out = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_hashing_embedder_is_deterministic():
    e = HashingEmbedder(dim=64)
    assert e.embed_query("hello world") == e.embed_query("hello world")


def test_hashing_embedder_dim_and_unit_length():
    e = HashingEmbedder(dim=128)
    v = e.embed_query("some text here")
    assert len(v) == 128
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)


def test_hashing_embedder_similarity_reflects_overlap():
    e = HashingEmbedder(dim=256)

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))  # unit vectors → dot == cosine

    base = e.embed_query("machine learning neural networks")
    similar = e.embed_query("neural networks and machine learning")
    different = e.embed_query("banana smoothie recipe")

    assert cos(base, similar) > cos(base, different)


def test_embed_documents_matches_query_embedding():
    e = HashingEmbedder(dim=32)
    (doc,) = e.embed_documents(["identical text"])
    assert doc == e.embed_query("identical text")


def test_embed_documents_empty():
    assert HashingEmbedder(dim=16).embed_documents([]) == []


def test_get_embedder_hashing_and_protocol(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LIBRARIAN_EMBEDDER", raising=False)
    e = get_embedder(dim=64)  # no key → offline hashing
    assert isinstance(e, HashingEmbedder)
    assert isinstance(e, Embedder)
    assert e.dim == 64


def test_default_dim_constant():
    assert EMBED_DIM == 768
