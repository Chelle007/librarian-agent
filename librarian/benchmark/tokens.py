"""Per-request, per-phase token accounting for the A/B benchmark.

The benchmark's whole point is *where* token cost concentrates, so it's not
enough to count total tokens — we bucket each LLM call by phase (classification /
generation / groundedness). `MeteredLLMClient` wraps any `LLMClient` and records
every call into a `TokenTracker`, so no agent/generation code changes to be
measured; you just swap the client.

Counts use the model's real `usage_metadata` when the wrapped client exposes it
(`GeminiClient.last_usage`), and fall back to a chars/4 estimate otherwise — so
the harness produces sensible numbers offline (with a `FakeLLMClient`) and exact
numbers against live Gemini.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from librarian.llm.gemini_client import LLMClient

# Prompt marker used to bucket the classification call (see classifier._PROMPT_TEMPLATE).
_CLASSIFY_MARKER = "Classify the request"


def approx_tokens(text: str) -> int:
    """Rough token estimate (chars/4) — same convention as the chunker."""
    return len(text or "") // 4


def infer_phase(prompt: str, response_json: bool) -> str:
    """Bucket an LLM call by inspecting its prompt/flags.

    classify → the one JSON classification call; groundedness → the JSON
    fact-check on a generated answer; generation → the free-text RAG answer.
    """
    if _CLASSIFY_MARKER in prompt:
        return "classify"
    if response_json:
        return "groundedness"
    return "generation"


@dataclass
class TokenEvent:
    phase: str
    prompt_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.output_tokens


@dataclass
class TokenTracker:
    events: list[TokenEvent] = field(default_factory=list)

    def record(self, phase: str, *, prompt_tokens: int, output_tokens: int) -> None:
        self.events.append(TokenEvent(phase, prompt_tokens, output_tokens))

    def reset(self) -> None:
        self.events.clear()

    @property
    def calls(self) -> int:
        return len(self.events)

    @property
    def total_tokens(self) -> int:
        return sum(e.total for e in self.events)

    def by_phase(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e.phase] = out.get(e.phase, 0) + e.total
        return out

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "total_tokens": self.total_tokens,
            "by_phase": self.by_phase(),
        }


class MeteredLLMClient:
    """Wraps an `LLMClient`, recording token usage of every call into a tracker."""

    def __init__(self, inner: LLMClient, tracker: TokenTracker):
        self.inner = inner
        self.tracker = tracker

    def generate(
        self, prompt: str, *, system: str | None = None, response_json: bool = False
    ) -> str:
        phase = infer_phase(prompt, response_json)
        out = self.inner.generate(prompt, system=system, response_json=response_json)

        # Prefer the model's real usage when available; else estimate.
        usage = getattr(self.inner, "last_usage", None)
        if usage and usage.get("total"):
            prompt_tokens = usage.get("prompt") or approx_tokens(prompt) + approx_tokens(system or "")
            output_tokens = usage.get("output") or approx_tokens(out)
        else:
            prompt_tokens = approx_tokens(prompt) + approx_tokens(system or "")
            output_tokens = approx_tokens(out)

        self.tracker.record(phase, prompt_tokens=prompt_tokens, output_tokens=output_tokens)
        return out
