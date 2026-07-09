"""Detect when a proposed vault update contradicts what's already on disk.

Before applying an update, compare the existing note against the incoming change.
Real contradictions (e.g. boyfriend → girlfriend, conflicting dates) route to
``needs_clarification`` so the user can confirm — same pattern as delete confirm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from librarian.llm.gemini_client import LLMClient, parse_json


@dataclass
class ConflictResult:
    has_conflict: bool
    message: str = ""


def check_update_conflict(
    llm: LLMClient,
    *,
    existing_frontmatter: dict,
    existing_body: str,
    proposed_fields: dict | None,
    proposed_body: str | None,
    request: str,
) -> ConflictResult:
    """Return whether the update contradicts existing note content."""
    prompt = _CONFLICT_PROMPT.format(
        existing=_format_existing(existing_frontmatter, existing_body),
        proposed=_format_proposed(proposed_fields, proposed_body),
        request=request.strip(),
    )
    try:
        data = parse_json(llm.generate(prompt, system=_CONFLICT_SYSTEM, response_json=True))
    except ValueError:
        return ConflictResult(has_conflict=False)

    if not bool(data.get("conflict")):
        return ConflictResult(has_conflict=False)

    message = (data.get("message") or "").strip()
    if not message:
        message = "That conflicts with what's already in the note."
    return ConflictResult(has_conflict=True, message=message)


def _format_existing(frontmatter: dict, body: str) -> str:
    return f"Frontmatter: {json.dumps(frontmatter, ensure_ascii=False)}\n\nBody:\n{body.strip()}"


def _format_proposed(fields: dict | None, body: str | None) -> str:
    parts: list[str] = []
    if fields:
        parts.append(f"Frontmatter changes: {json.dumps(fields, ensure_ascii=False)}")
    if body and body.strip():
        parts.append(f"Body (after merge): {body.strip()}")
    return "\n".join(parts) or "(no structured changes extracted)"


_CONFLICT_SYSTEM = (
    "You check whether a proposed vault update contradicts existing note content. "
    "Reply with JSON only."
)
_CONFLICT_PROMPT = """\
Does the REQUEST contradict what's already in the EXISTING note?

A contradiction means the new info cannot both be true at once — e.g.:
- relationship boyfriend vs girlfriend
- male vs female pronouns or gender for the same person
- conflicting dates, names, or opposite facts

NOT a contradiction: adding new info, elaborating, correcting a typo, or replacing
empty/missing fields when nothing conflicts.

EXISTING NOTE:
{existing}

PROPOSED UPDATE:
{proposed}

REQUEST:
{request}

Return JSON: {{"conflict": true|false, "message": "<short explanation for the user, or empty if no conflict>"}}
"""
