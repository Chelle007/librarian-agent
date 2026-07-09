"""Recover pending vault mutations from conversation context.

When the user confirms a prior clarification, the confirmation turn often
carries no new facts (e.g. "sure"). These helpers decide whether context
describes what to write and, when it does, extract fields/body to apply:

- ``recover_pending_update`` — path-specific updates (conflict confirm).
- ``recover_pending_mutation`` — create/update after mention or target gates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from librarian.classifier import Classification
from librarian.llm.gemini_client import LLMClient, parse_json


@dataclass
class PendingUpdate:
    sufficient: bool
    message: str = ""
    fields: dict = field(default_factory=dict)
    body: str | None = None


def has_proposed_changes(c: Classification, raw_request: str) -> bool:
    """True when the classification already carries concrete update content."""
    if c.fields:
        return True
    body = (c.body or "").strip()
    request = (raw_request or "").strip()
    return bool(body and body != request)


def recover_pending_update(
    llm: LLMClient,
    *,
    context: str | None,
    path: str,
    existing_frontmatter: dict,
    existing_body: str,
    request: str,
) -> PendingUpdate:
    """Extract the pending update from context, or report insufficient detail."""
    prompt = _RECOVER_PROMPT.format(
        context=context or "(none)",
        path=path,
        existing_frontmatter=json.dumps(existing_frontmatter, ensure_ascii=False),
        existing_body=existing_body.strip(),
        request=request.strip(),
    )
    try:
        data = parse_json(llm.generate(prompt, system=_RECOVER_SYSTEM, response_json=True))
    except ValueError:
        return PendingUpdate(
            sufficient=False,
            message="I know which note you mean, but not what to change.",
        )

    sufficient = bool(data.get("sufficient"))
    message = (data.get("message") or "").strip()
    if not sufficient and not message:
        message = (
            "I know which note you mean, but the conversation doesn't say what to change. "
            "Tell me what to update."
        )
    body = data.get("body")
    return PendingUpdate(
        sufficient=sufficient,
        message=message,
        fields=data.get("fields") or {},
        body=body.strip() if isinstance(body, str) and body.strip() else None,
    )


@dataclass
class PendingMutation:
    sufficient: bool
    message: str = ""
    note_type: str | None = None
    fields: dict = field(default_factory=dict)
    body: str | None = None


def is_pending_mutation_context(context: str | None) -> bool:
    if not context:
        return False
    low = context.lower()
    markers = (
        "mentioned elsewhere",
        "confirm to save and link",
        "confirm to update and link",
        "confirm if you want to update",
        "confirm to update anyway",
        "confirm to proceed",
        "confirm to update this note",
    )
    return any(m in low for m in markers)


def recover_pending_mutation(
    llm: LLMClient,
    *,
    context: str | None,
    request: str,
) -> PendingMutation:
    """Extract the pending create/update the user is confirming from context."""
    prompt = _MUTATION_PROMPT.format(
        context=context or "(none)",
        request=request.strip(),
    )
    try:
        data = parse_json(llm.generate(prompt, system=_RECOVER_SYSTEM, response_json=True))
    except ValueError:
        return PendingMutation(
            sufficient=False,
            message="I lost track of what to save. Repeat the fact you want in the vault.",
        )

    sufficient = bool(data.get("sufficient"))
    message = (data.get("message") or "").strip()
    if not sufficient and not message:
        message = "I lost track of what to save. Repeat the fact you want in the vault."
    body = data.get("body")
    return PendingMutation(
        sufficient=sufficient,
        message=message,
        note_type=data.get("note_type") or None,
        fields=data.get("fields") or {},
        body=body.strip() if isinstance(body, str) and body.strip() else None,
    )


def apply_pending_mutation(c: Classification, pending: PendingMutation) -> Classification:
    if pending.note_type:
        c.note_type = pending.note_type
    if pending.fields:
        merged = dict(c.fields)
        merged.update(pending.fields)
        c.fields = merged
    if pending.body:
        c.body = pending.body
    return c


_RECOVER_SYSTEM = (
    "You extract pending vault updates from conversation context. Reply with JSON only."
)

_MUTATION_PROMPT = """\
The user is confirming a pending vault CREATE or UPDATE after a librarian clarification.

CONTEXT should contain the user's original fact (e.g. "my bestie is Angeline") and/or
the librarian's clarification (mentions elsewhere, conflict, which note to update).
Extract what should be written to the vault. A bare "yes" with no recoverable fact
in context → sufficient is false.

CONTEXT:
{context}

USER REQUEST (often a short confirmation):
{request}

Return JSON:
- "sufficient": true when context states what note to create/update and key fields
- "message": clarifying question when sufficient is false (else empty)
- "note_type": contact | habit | task | note | etc., or null
- "fields": frontmatter fields to set (e.g. name, relationship, due_date)
- "body": note body text, or null
"""

_RECOVER_PROMPT = """\
The user is confirming a pending vault UPDATE to an existing note.

CONTEXT must describe the specific change waiting for approval — e.g. the user's
original fact statement and/or the librarian's clarification about a conflict.
If context only names a note path (or says "confirm update") without saying
WHAT field or body should change, sufficient is false.

CONTEXT:
{context}

NOTE PATH:
{path}

EXISTING FRONTMATTER:
{existing_frontmatter}

EXISTING BODY:
{existing_body}

USER REQUEST (often a short confirmation):
{request}

Return JSON:
- "sufficient": true only when context clearly states what should be written
- "message": clarifying question when sufficient is false (else empty)
- "fields": frontmatter fields to set (object, may be empty)
- "body": body text to set or append (string or null)
"""
