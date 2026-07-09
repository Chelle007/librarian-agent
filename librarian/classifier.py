"""Intent + retrieval-mode classification (one LLM call) and confidence scoring.

Every free-text request entering `librarian_handle` is routed here first. The
design (see Architecture, "Intent Classification & Confidence"):

- **One combined LLM call** returns the intent (`create`/`update`/`query`/`delete`),
  and for queries the retrieval mode (`exact_lookup`/`semantic`/`hybrid`), plus
  everything the router needs downstream (note type + fields for creates,
  extracted filters + query text for queries, a target reference for
  update/delete, suggested wikilinks, and a correction flag).
- **Confidence is a heuristic, not an LLM self-report.** Self-reported confidence
  was found to cluster near 0.9 regardless of correctness, so it's computed here
  from two cheap deterministic signals: the vector margin (top-1 vs top-2 gap)
  and the target-candidate count. Below a threshold → the router asks to clarify.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from librarian.llm.gemini_client import LLMClient, parse_json
from librarian.store.schema import Schema

INTENTS = ("create", "update", "query", "delete")
MODES = ("exact_lookup", "semantic", "hybrid")

# Confidence gate: below this, the router routes to needs_clarification.
CONFIDENCE_THRESHOLD = 0.35
# A top-1/top-2 similarity gap at or above this reads as an unambiguous winner.
STRONG_MARGIN = 0.15


@dataclass
class Classification:
    intent: str
    mode: str | None = None  # retrieval mode, for query intent only
    note_type: str | None = None  # for create
    fields: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    body: str | None = None  # note body for create/update
    query_text: str | None = None  # search text for query modes
    filters: dict = field(default_factory=dict)  # type/tag/keyword/date/aggregate
    target_ref: str | None = None  # reference phrase for update/delete
    is_reaction: bool = False  # user is correcting prior librarian output
    actionable: bool = True  # false → router asks before mutating or querying
    clarify_message: str = ""
    raw: dict = field(default_factory=dict)


class Classifier:
    """Wraps the single combined-classification LLM call."""

    def __init__(self, llm: LLMClient, schema: Schema):
        self.llm = llm
        self.schema = schema

    def classify(self, raw_request: str, context: str | None = None) -> Classification:
        prompt = self._build_prompt(raw_request, context)
        try:
            data = parse_json(self.llm.generate(prompt, system=_SYSTEM, response_json=True))
        except ValueError:
            # Unparseable → treat as ambiguous; the router will ask to clarify.
            return Classification(intent="query", mode="semantic", query_text=raw_request, raw={})
        return self._from_json(data, raw_request)

    # ------------------------------------------------------------- prompt / parse
    def _build_prompt(self, raw_request: str, context: str | None) -> str:
        types = ", ".join(sorted(self.schema.types))
        ctx = f"\nRecent conversation (for coreference):\n{context}\n" if context else ""
        return _PROMPT_TEMPLATE.format(types=types, context=ctx, request=raw_request)

    def _from_json(self, data: dict, raw_request: str) -> Classification:
        intent = data.get("intent")
        if intent not in INTENTS:
            intent = "query"  # safest fallback: read, don't mutate

        mode = data.get("mode") if intent == "query" else None
        if intent == "query" and mode not in MODES:
            mode = "semantic"

        is_reaction = bool(data.get("is_reaction"))

        note_type = data.get("note_type") or None
        actionable = data.get("actionable", True)
        if actionable is False or actionable == "false":
            actionable = False
        else:
            actionable = True
        clarify_message = (data.get("clarify_message") or data.get("message") or "").strip()
        if not actionable and not clarify_message:
            clarify_message = "I need a bit more context before I can do that."
        return Classification(
            intent=intent,
            mode=mode,
            note_type=note_type,
            fields=data.get("fields") or {},
            tags=_as_list(data.get("tags")),
            links=_as_list(data.get("links")),
            body=data.get("body") or None,
            query_text=data.get("query_text") or raw_request,
            filters=data.get("filters") or {},
            target_ref=data.get("target_ref") or None,
            is_reaction=is_reaction,
            actionable=actionable,
            clarify_message=clarify_message,
            raw=data,
        )


# --------------------------------------------------------------- confidence
def vector_margin(hits: list) -> float:
    """Similarity gap between the top-1 and top-2 hits (1.0 if fewer than 2).

    A large gap means one clear winner; a small gap means several notes match
    about equally well — i.e. genuine retrieval ambiguity.
    """
    if len(hits) < 2:
        return 1.0
    return max(0.0, hits[0].score - hits[1].score)


def confidence_from_margin(hits: list) -> float:
    """Map the vector margin onto a [0, 1] confidence (saturating at STRONG_MARGIN)."""
    if not hits:
        return 0.0
    return min(1.0, vector_margin(hits) / STRONG_MARGIN)


def confidence_from_candidates(candidate_count: int) -> float:
    """Exactly one plausible target → confident; zero or many → not."""
    return 1.0 if candidate_count == 1 else 0.0


def is_confident(score: float) -> bool:
    return score >= CONFIDENCE_THRESHOLD


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


_SYSTEM = (
    "You are the routing brain of a personal knowledge vault. You classify a "
    "single user request and extract structured routing data. Reply with JSON only."
)

_PROMPT_TEMPLATE = """\
Classify the request below into exactly one intent and extract routing data.

Known note types: {types} (unknown types are allowed; they fall back to `note`).

Return JSON with these keys:
- "intent": one of create | update | query | delete
- "mode": for query only, one of exact_lookup | semantic | hybrid
    (exact_lookup = structured/keyword filter or a count; semantic = meaning-based
     content search; hybrid = content search constrained by a type/tag/date filter)
- "note_type": for create/update, the note type (or null)
- "fields": object of frontmatter fields to set (e.g. name, due_date, rating).
    For typed facts, set note_type and the schema's identity fields for that type
    (e.g. contact.name, habit.name+frequency, task.due_date).
- "tags": array of tags
- "links": array of wikilink targets (note stems) suggested from content; merged on write
- "body": the note's prose body, for create/update (or null)
- "query_text": the natural-language search text, for query (or null)
- "filters": for exact_lookup/hybrid — object with any of
    type, tag, keyword, created_after (YYYY-MM-DD), created_before (YYYY-MM-DD),
    aggregate (true for "how many"). Convert relative times ("last month") to dates.
- "target_ref": for update/delete, the phrase identifying which note (or null)
- "is_reaction": true when the user is correcting or revising prior librarian
    output — wrong fact, wrong gender/type, "actually…", "/correct_librarian…".
    false for first-time facts, plain elaboration, greetings, or queries.
- "actionable": false when the request cannot be executed without more user input
    — e.g. a short reply with no conversation context explaining what to confirm;
    greetings or off-topic chitchat with no vault content; a follow-up that only
    makes sense if context contained a pending librarian question but does not.
    true when self-contained or context supplies what is needed.
- "clarify_message": short question for the user when actionable is false (else "")
{context}
Request:
{request}
"""
