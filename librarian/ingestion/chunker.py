"""Chunking — Policy v1 (see `docs/Embedding & Chunking Decision (Jul 7).md`).

A note is split into one or more chunks for embedding. The common case is a
single whole-note chunk; only long freeform notes are split. The vector store is
chunk-native, so a whole note is simply a note that produced exactly one chunk
(chunk_index 0) — changing this policy later is a re-embed, never a migration.

Policy:
- Structured / short types (contact, task, habit, brief): always whole-note.
- Freeform types (note, journal, ...): whole-note under the threshold; above it,
  a structure-aware split on blank-line paragraph boundaries with light overlap,
  each chunk hard-capped under the model's input limit.

Token counts are rough (chars/4) — exact tokenization isn't worth the dependency
here; the caps are conservative enough to stay well under the 2048-token model
limit regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Types whose content is small and atomic — never worth splitting.
WHOLE_NOTE_TYPES: frozenset[str] = frozenset({"contact", "task", "habit", "brief"})

# Rough token budget knobs (chars ≈ tokens * 4).
FREEFORM_THRESHOLD_TOKENS = 1000  # under this, a freeform note stays whole
TARGET_TOKENS = 650               # aim per chunk when splitting
MAX_TOKENS = 1800                 # hard cap per chunk (safely under the 2048 model limit)
OVERLAP_TOKENS = 90               # carry-over between adjacent chunks (~14%)

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. Deliberately rough; only used for chunk sizing."""
    return max(1, len(text) // 4)


def chunk_note(note_type: str | None, text: str) -> list[Chunk]:
    """Split `text` into embeddable chunks per Policy v1.

    `text` is the already-composed embed text for the note (title/name + body +
    salient fields) — this module doesn't know about frontmatter.
    """
    text = (text or "").strip()
    if not text:
        return []

    if note_type in WHOLE_NOTE_TYPES or estimate_tokens(text) <= FREEFORM_THRESHOLD_TOKENS:
        return [Chunk(0, text)]

    return [Chunk(i, t) for i, t in enumerate(_structure_aware_split(text))]


def _structure_aware_split(text: str) -> list[str]:
    paras = [p.strip() for p in _PARA_SPLIT_RE.split(text) if p.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paras:
        para_tokens = estimate_tokens(para)

        # An oversized single paragraph can't share a chunk — flush, then hard-split it.
        if para_tokens > MAX_TOKENS:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            chunks.extend(_hard_split(para))
            continue

        # Adding this paragraph would overflow the target — close the chunk and
        # start the next one seeded with an overlap tail for continuity.
        if current and current_tokens + para_tokens > TARGET_TOKENS:
            chunks.append("\n\n".join(current))
            current = _overlap_tail(current)
            current_tokens = sum(estimate_tokens(p) for p in current)

        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _overlap_tail(paragraphs: list[str]) -> list[str]:
    """Keep trailing paragraph(s) up to the overlap budget, to seed the next chunk."""
    tail: list[str] = []
    tokens = 0
    for para in reversed(paragraphs):
        pt = estimate_tokens(para)
        if tail and tokens + pt > OVERLAP_TOKENS:
            break
        tail.insert(0, para)
        tokens += pt
        if tokens >= OVERLAP_TOKENS:
            break
    return tail


def _hard_split(paragraph: str) -> list[str]:
    """Split a single over-long paragraph by words into MAX_TOKENS-sized pieces."""
    words = paragraph.split()
    max_chars = MAX_TOKENS * 4

    pieces: list[str] = []
    buf: list[str] = []
    length = 0
    for word in words:
        add = len(word) + (1 if buf else 0)
        if buf and length + add > max_chars:
            pieces.append(" ".join(buf))
            buf, length = [], 0
            add = len(word)
        buf.append(word)
        length += add
    if buf:
        pieces.append(" ".join(buf))
    return pieces
