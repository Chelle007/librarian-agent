"""Compose the text that represents a note for embedding.

A note is frontmatter + markdown body, but a raw dump of either isn't the best
embedding target: structured notes (a contact) carry their meaning in *fields*
(name, likes, birthday), while freeform notes carry it in the body. This module
is the single place that decides what text stands in for a note, so the write
pipeline (indexing on create/update) and the eval harness (indexing the whole
vault) embed notes identically — a mismatch there would silently poison recall.

Bookkeeping fields (`type`, dates) are dropped — they're filtered structurally
via the metadata store, not semantically, so embedding them only adds noise.
"""

from __future__ import annotations

# Frontmatter keys that carry no semantic signal for retrieval — they're handled
# by the structured/metadata path, so including them would just dilute the vector.
_SKIP_FIELDS: frozenset[str] = frozenset(
    {"type", "created_date", "last_modified", "captured_at", "note_id"}
)

# Rendered first (as a bare title line), not as "name: ..." / "title: ...".
_TITLE_FIELDS: tuple[str, ...] = ("title", "name")


def note_embed_text(frontmatter: dict, body: str = "") -> str:
    """Flatten a note into a single embed string: title, salient fields, body.

    Scalars and lists become compact `key: value` lines; nested dicts are skipped
    (no meaningful flat text). The asymmetric-retrieval prefix (`title: … | text: …`)
    is applied later, in the embedder — this returns the bare content.
    """
    parts: list[str] = []

    for key in _TITLE_FIELDS:
        value = frontmatter.get(key)
        if value:
            parts.append(str(value))
            break

    for key, value in frontmatter.items():
        if key in _SKIP_FIELDS or key in _TITLE_FIELDS:
            continue
        flat = _flatten_value(value)
        if flat:
            parts.append(f"{key}: {flat}")

    body = (body or "").strip()
    if body:
        parts.append(body)

    return "\n".join(parts).strip()


def _flatten_value(value) -> str:
    """Render a frontmatter value as compact text, or '' if it carries nothing."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(items)
    if isinstance(value, dict):
        return ""  # nested structure has no useful flat embedding text
    return str(value).strip()
