"""Template rendering for `exact_lookup` responses (no LLM).

The `exact_lookup` path has an exact answer straight from the index, so its
reply is a deterministic f-string, not an LLM generation — no hallucination risk,
no token cost. All the templating lives here, in one file, on purpose: the
architecture flags dissatisfaction with f-string templates as an open item, so
keeping them isolated means swapping the rendering approach later touches only
this module, never the retrieval logic.

Renderers duck-type on a "hit": anything exposing `.type`, `.title`,
`.created_date`, and a `.frontmatter` dict (see `exact_lookup.LookupHit`).
"""

from __future__ import annotations


def render_results(hits: list, *, keyword: str | None = None) -> str:
    """Human-readable summary of lookup hits (0, 1, or many)."""
    if not hits:
        return _no_results(keyword)
    if len(hits) == 1:
        return summarize_hit(hits[0])

    lines = [f"Found {len(hits)} matches:"]
    lines.extend(f"- {summarize_hit(h)}" for h in hits)
    return "\n".join(lines)


def render_aggregation(
    note_type: str, strict_count: int, tag_count: int, *, tag: str | None = None
) -> str:
    """Render an aggregation count, surfacing a strict-vs-tag discrepancy.

    Schema-on-read routes unknown content to the `note` fallback bucket, so a
    strict `type`-only count can silently undercount things that were only ever
    tagged. Rather than return a confidently-wrong number, we run both counts and
    flag the gap for the user to resolve (see Architecture, Retrieval Taxonomy).
    """
    tag_label = tag or note_type
    base = f"Found {strict_count} {_plural(note_type, strict_count)} by type"
    extra = tag_count - strict_count
    if extra > 0:
        return (
            f"{base}, but {extra} more note{'s' if extra != 1 else ''} "
            f"tagged '{tag_label}' aren't typed as {note_type} — include them?"
        )
    return f"{base}."


def summarize_hit(hit) -> str:
    """One-line, type-aware summary of a single note."""
    return _SUMMARIZERS.get(hit.type, _summarize_default)(hit)


# --------------------------------------------------------------- per-type lines
def _summarize_contact(hit) -> str:
    fm = hit.frontmatter
    bits = []
    if fm.get("birthday"):
        bits.append(f"birthday {fm['birthday']}")
    likes = fm.get("likes")
    if likes:
        bits.append(f"likes {_join(likes)}")
    detail = f" — {', '.join(bits)}" if bits else ""
    return f"{hit.title}{detail}"


def _summarize_task(hit) -> str:
    fm = hit.frontmatter
    parts = [hit.title]
    if fm.get("due_date"):
        parts.append(f"due {fm['due_date']}")
    if fm.get("status"):
        parts.append(str(fm["status"]))
    return " — ".join(parts)


def _summarize_habit(hit) -> str:
    fm = hit.frontmatter
    parts = [hit.title]
    if fm.get("frequency"):
        parts.append(f"every {fm['frequency']}")
    streak = fm.get("current_streak")
    if streak is not None:
        parts.append(f"streak {streak}")
    return " — ".join(parts)


def _summarize_default(hit) -> str:
    when = f" ({hit.created_date})" if hit.created_date else ""
    return f"{hit.title}{when}"


_SUMMARIZERS = {
    "contact": _summarize_contact,
    "task": _summarize_task,
    "habit": _summarize_habit,
}


# ---------------------------------------------------------------------- helpers
def _no_results(keyword: str | None) -> str:
    if keyword:
        return f"No notes found matching '{keyword}'."
    return "No matching notes found."


def _join(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _plural(word: str, n: int) -> str:
    return word if n == 1 else f"{word}s"
