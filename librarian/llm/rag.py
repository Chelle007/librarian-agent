"""RAG answer generation + groundedness verification (semantic/hybrid paths only).

Once the semantic or hybrid path has retrieved the top notes, this turns them
into a grounded natural-language answer:

1. **generate_answer** — one LLM call answering the question using *only* the
   retrieved chunk text as source material.
2. **check_groundedness** — a second LLM call that verifies the answer is
   supported by those chunks, and rewrites it to supported content if not. This
   is the architecture's explicit hallucination guard, applied here (generation
   paths) and never on `exact_lookup` (which has no generation to hallucinate).

Both take an `LLMClient`, so they run against real Gemini in production and a
`FakeLLMClient` in tests. Retrieval hits are passed in (each exposing `.text`
and `.note_path`) — this module never touches the vector store itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from librarian.llm.gemini_client import LLMClient, parse_json


@dataclass
class GroundednessResult:
    grounded: bool
    answer: str  # the original answer if grounded, else the rewrite (or a hedge)


def _format_sources(hits: list) -> str:
    """Number the retrieved chunks so the answer/verification can cite them."""
    return "\n\n".join(f"[{i + 1}] ({h.note_path})\n{h.text}" for i, h in enumerate(hits))


def generate_answer(llm: LLMClient, question: str, hits: list) -> str:
    """Answer `question` grounded strictly in the retrieved chunks."""
    if not hits:
        return "I couldn't find anything in your vault about that."
    prompt = _ANSWER_PROMPT.format(question=question, sources=_format_sources(hits))
    return llm.generate(prompt, system=_ANSWER_SYSTEM).strip()


def check_groundedness(llm: LLMClient, answer: str, hits: list) -> GroundednessResult:
    """Verify `answer` against the sources; rewrite to supported content if not.

    Returns the original answer when grounded, otherwise the model's rewrite
    (falling back to a hedge if the rewrite is empty). Fails open — a parse error
    keeps the original answer rather than blocking a probably-fine response.
    """
    if not hits:
        return GroundednessResult(grounded=True, answer=answer)

    prompt = _GROUNDEDNESS_PROMPT.format(answer=answer, sources=_format_sources(hits))
    try:
        data = parse_json(llm.generate(prompt, system=_GROUNDEDNESS_SYSTEM, response_json=True))
    except ValueError:
        return GroundednessResult(grounded=True, answer=answer)

    if bool(data.get("grounded")):
        return GroundednessResult(grounded=True, answer=answer)

    revised = (data.get("revised") or "").strip()
    return GroundednessResult(
        grounded=False,
        answer=revised or "I don't have enough in your vault to answer that confidently.",
    )


_ANSWER_SYSTEM = (
    "You answer questions using only the provided notes from the user's personal "
    "vault. Never invent facts not present in the sources. Be concise."
)
_ANSWER_PROMPT = """\
Answer the question using only the sources below. If the sources don't contain
the answer, say so plainly.

Question: {question}

Sources:
{sources}
"""

_GROUNDEDNESS_SYSTEM = (
    "You are a strict fact-checker. Decide whether an answer is fully supported by "
    "the provided sources. Reply with JSON only."
)
_GROUNDEDNESS_PROMPT = """\
Is every claim in the ANSWER supported by the SOURCES?

Return JSON: {{"grounded": true|false, "revised": "<answer rewritten to only
supported content, or empty string if grounded>"}}

ANSWER:
{answer}

SOURCES:
{sources}
"""
