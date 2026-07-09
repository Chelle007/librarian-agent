"""Find vault notes that mention an identity label in their content."""

from __future__ import annotations

import re
from dataclasses import dataclass

from librarian.store.metadata_store import MetadataStore
from librarian.store.vault_io import VaultIO, VaultIOError


@dataclass
class Mention:
    path: str
    snippet: str


def find_mentions(
    label: str,
    meta: MetadataStore,
    vault: VaultIO,
    *,
    exclude: set[str] | None = None,
    limit: int = 5,
) -> list[Mention]:
    """Notes whose body or frontmatter contain ``label`` as a whole token."""
    text_label = (label or "").strip()
    if not text_label:
        return []

    pattern = re.compile(r"\b" + re.escape(text_label) + r"\b", re.IGNORECASE)
    skip = exclude or set()
    hits: list[Mention] = []

    for row in meta.query():
        path = row["path"]
        if path in skip:
            continue
        try:
            note = vault.read(path)
        except VaultIOError:
            continue
        haystack = _note_text(note.frontmatter, note.body)
        match = pattern.search(haystack)
        if not match:
            continue
        hits.append(Mention(path=path, snippet=_snippet(haystack, match.start())))
        if len(hits) >= limit:
            break
    return hits


def identity_label(fields: dict) -> str | None:
    """Primary searchable label from classifier identity fields."""
    name = (fields.get("name") or "").strip()
    if name:
        return name
    for value in fields.values():
        text = str(value).strip()
        if text:
            return text
    return None


def _note_text(frontmatter: dict, body: str) -> str:
    parts = [str(v) for v in frontmatter.values() if v is not None]
    parts.append(body or "")
    return "\n".join(parts)


def _snippet(text: str, index: int, *, radius: int = 40) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + radius)
    chunk = " ".join(text[start:end].split())
    if start > 0:
        chunk = "…" + chunk
    if end < len(text):
        chunk = chunk + "…"
    return chunk
