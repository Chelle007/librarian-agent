"""Assess whether a request has enough context to act on — no word-list heuristics.

Used when rule-prefilter bypasses the main classifier, and embedded in the
classifier JSON for the full LLM path. Returns whether the request is actionable,
a clarifying message when it is not, and whether it confirms a pending action
described in conversation context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from librarian.llm.gemini_client import LLMClient, parse_json


@dataclass
class ContextAssessment:
    actionable: bool
    message: str = ""
    is_confirmation: bool = False


def assess_context(
    llm: LLMClient,
    *,
    request: str,
    context: str | None,
    intent: str,
    target_ref: str | None = None,
) -> ContextAssessment:
    """Decide if the request can be executed given available context."""
    prompt = _ASSESS_PROMPT.format(
        context=context or "(none)",
        intent=intent,
        target_ref=target_ref or "(none)",
        request=request.strip(),
    )
    try:
        data = parse_json(llm.generate(prompt, system=_ASSESS_SYSTEM, response_json=True))
    except ValueError:
        return ContextAssessment(actionable=True)

    actionable = bool(data.get("actionable", True))
    message = (data.get("message") or "").strip()
    if not actionable and not message:
        message = "I need a bit more context before I can do that."
    return ContextAssessment(
        actionable=actionable,
        message=message,
        is_confirmation=bool(data.get("is_confirmation")),
    )


def context_references_path(context: str | None, path: str) -> bool:
    """True when conversation context ties to a specific note path."""
    if not context:
        return False
    if path in context:
        return True
    name = Path(path).name
    return bool(name and name in context)


_ASSESS_SYSTEM = (
    "You judge whether a personal vault assistant has enough context to act. "
    "Reply with JSON only."
)

_ASSESS_PROMPT = """\
Does the REQUEST give enough context to execute the proposed vault action?

Proposed action:
- intent: {intent}
- target_ref: {target_ref}

Recent conversation:
{context}

REQUEST:
{request}

Return JSON:
- "actionable": false when the request cannot be executed as-is — e.g. a short
  reply or acknowledgment with no conversation context explaining what to confirm;
  greetings or off-topic messages with no vault information to save or change;
  a follow-up that only makes sense after a librarian clarification that is NOT
  present in the conversation context.
- "message": a short clarifying question when actionable is false (else empty)
- "is_confirmation": true only when the user is affirming or approving a pending
  vault action that IS described in the conversation context (confirming a delete,
  approving a conflicting update, etc.). Requires context to describe the SPECIFIC
  pending change — not just a note path. false otherwise.
"""
