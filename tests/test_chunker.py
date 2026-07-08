"""Tests for the chunker (Policy v1)."""

from __future__ import annotations

from librarian.ingestion import chunker
from librarian.ingestion.chunker import MAX_TOKENS, Chunk, chunk_note, estimate_tokens


def test_empty_text_yields_no_chunks():
    assert chunk_note("note", "") == []
    assert chunk_note("note", "   \n  ") == []


def test_structured_type_is_always_whole_note():
    long_text = "word " * 5000  # well over the freeform threshold
    chunks = chunk_note("contact", long_text)
    assert len(chunks) == 1
    assert chunks[0].index == 0


def test_short_freeform_note_is_whole_note():
    chunks = chunk_note("note", "a short idea about testing")
    assert chunks == [Chunk(0, "a short idea about testing")]


def test_long_freeform_note_is_split():
    # many distinct paragraphs, comfortably over the threshold
    paras = [f"Paragraph number {i} " + ("filler " * 40) for i in range(30)]
    text = "\n\n".join(paras)
    assert estimate_tokens(text) > 1000

    chunks = chunk_note("note", text)
    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # every chunk stays under the hard cap
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)


def test_oversized_single_paragraph_is_hard_split():
    giant = "lorem " * (MAX_TOKENS * 4)  # one paragraph far above MAX_TOKENS
    chunks = chunk_note("note", giant)
    assert len(chunks) > 1
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)


def test_split_has_overlap_between_chunks(monkeypatch):
    # Force small budgets so a handful of distinct paragraphs must split with overlap.
    monkeypatch.setattr(chunker, "FREEFORM_THRESHOLD_TOKENS", 5)
    monkeypatch.setattr(chunker, "TARGET_TOKENS", 12)
    monkeypatch.setattr(chunker, "OVERLAP_TOKENS", 8)

    paras = [f"alpha{i} beta{i} gamma{i} delta{i}" for i in range(6)]
    chunks = chunker.chunk_note("note", "\n\n".join(paras))

    assert len(chunks) >= 2
    # consecutive chunks should share at least one paragraph (overlap seed)
    first_paras = set(chunks[0].text.split("\n\n"))
    second_paras = set(chunks[1].text.split("\n\n"))
    assert first_paras & second_paras
