"""Resolve which existing note an update/delete refers to.

Update ("rate that movie 5 stars") and delete ("remove the note about X") both
have to turn a fuzzy reference into a concrete note path. Shared strategy
(see Architecture / Build Plan — "context → recency heuristic → clarification"):

1. If the reference is already an explicit vault path that exists, use it.
2. Otherwise semantic-search for it (folding in recent conversation `context` for
   coreference), and take the cluster of hits within one strong-margin band of the
   top result as the plausible candidates.
3. Among that cluster, the **recency** heuristic picks a best guess (most recently
   modified) — used as the suggested target in a clarification prompt.

Confidence is the candidate count (exactly one → confident; zero or several →
not), so the router can gate on it: a lone candidate resolves cleanly, an
ambiguous cluster routes to `needs_clarification` rather than guessing silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from librarian.classifier import STRONG_MARGIN, confidence_from_candidates
from librarian.store.metadata_store import MetadataStore


@dataclass
class TargetResolution:
    path: str | None  # best-guess target (recency-resolved within the cluster)
    candidates: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def resolved(self) -> bool:
        return self.path is not None and len(self.candidates) == 1


def resolve_target(
    target_ref: str | None,
    *,
    meta: MetadataStore,
    retriever,
    context: str | None = None,
    k: int = 5,
) -> TargetResolution:
    """Resolve a reference phrase to a note path (see module docstring)."""
    ref = (target_ref or "").strip()

    # 1. explicit path that actually exists in the index (incl. emoji-folder suffixes)
    existing = resolve_existing_path(ref, meta)
    if existing:
        return TargetResolution(path=existing, candidates=[existing], confidence=1.0)

    # 2. semantic search, coreference-resolved via context
    query = " ".join(p for p in (ref, context) if p).strip()
    if not query:
        return TargetResolution(path=None, candidates=[], confidence=0.0)

    hits = retriever.search(query, k=k)
    if not hits:
        return TargetResolution(path=None, candidates=[], confidence=0.0)

    top = hits[0].score
    cluster = [h.note_path for h in hits if top - h.score <= STRONG_MARGIN]

    # 3. recency tie-break for the best guess (still gated by candidate count)
    primary = recency_pick(cluster, meta) or hits[0].note_path
    return TargetResolution(
        path=primary,
        candidates=cluster,
        confidence=confidence_from_candidates(len(cluster)),
    )


def resolve_existing_path(ref: str, meta: MetadataStore) -> str | None:
    """Resolve a vault path, including emoji-prefixed folders and suffix matches."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if meta.get(ref) is not None:
        return ref

    basename = Path(ref).name
    matches = [
        row["path"]
        for row in meta.query()
        if row["path"] == ref
        or row["path"].endswith("/" + ref)
        or Path(row["path"]).name == basename
    ]
    if not matches:
        return None

    suffix = [p for p in matches if p.endswith(ref) or p.endswith("/" + ref)]
    if len(suffix) == 1:
        return suffix[0]
    if len(matches) == 1:
        return matches[0]
    return None


def recency_pick(paths: list[str], meta: MetadataStore) -> str | None:
    """Return the most recently modified of `paths` (recency heuristic)."""
    rows = [r for p in paths if (r := meta.get(p)) is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return rows[0]["path"]
