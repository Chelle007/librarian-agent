"""LLM transport — the single place text generations are produced.

Mirrors the split in `embeddings.py`: one interface, two implementations.
- `GeminiClient` — the real Gemini Flash call (needs an API key + network).
- `FakeLLMClient` — deterministic, offline, no dependency. Returns scripted or
  handler-computed responses so the classifier, RAG generation, and groundedness
  check are all unit-testable without the API.

This module owns *transport only* (prompt in → text out, optional JSON mode).
Prompt construction and output parsing live with each caller (classifier, RAG),
so this stays a thin, swappable seam — same reasoning as keeping the embed call
isolated in its own helper.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Protocol, runtime_checkable

# Gemini Flash per the architecture's hard model constraint (no Pro upgrade).
GEMINI_FLASH_MODEL = "gemini-3.5-flash"

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@runtime_checkable
class LLMClient(Protocol):
    def generate(
        self, prompt: str, *, system: str | None = None, response_json: bool = False
    ) -> str: ...


def parse_json(text: str) -> dict:
    """Parse an LLM JSON reply, tolerating ```json fences and surrounding prose.

    Models occasionally wrap JSON in a code fence or add a stray sentence even
    when asked not to; we strip fences and, failing that, grab the outermost
    brace span. Raises ValueError if nothing parses — callers treat that as an
    ambiguous/failed classification rather than crashing.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")

    stripped = _JSON_FENCE_RE.sub("", text).strip()
    for candidate in (stripped, _outermost_braces(stripped)):
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from LLM response: {text[:200]!r}")


def _outermost_braces(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return None


class GeminiClient:
    """Production LLM: Gemini Flash via `google-genai` (lazy-imported)."""

    def __init__(self, api_key: str | None = None, model: str = GEMINI_FLASH_MODEL):
        try:
            from google import genai  # noqa: PLC0415 (lazy by design)
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "google-genai is required for GeminiClient (`pip install google-genai`), "
                "or use FakeLLMClient for offline dev/tests"
            ) from exc

        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model
        # Last call's token usage, for the token A/B benchmark (Build Plan).
        self.last_usage: dict | None = None

    def generate(
        self, prompt: str, *, system: str | None = None, response_json: bool = False
    ) -> str:  # pragma: no cover - network
        from google.genai import types  # noqa: PLC0415

        config_kwargs: dict = {}
        if system:
            config_kwargs["system_instruction"] = system
        if response_json:
            config_kwargs["response_mime_type"] = "application/json"

        resp = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
        )
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            self.last_usage = {
                "prompt": getattr(usage, "prompt_token_count", None),
                "output": getattr(usage, "candidates_token_count", None),
                "total": getattr(usage, "total_token_count", None),
            }
        return resp.text or ""


class FakeLLMClient:
    """Offline, deterministic LLM stand-in for tests and scripted demos.

    Give it either a queue of `responses` (popped in order) or a `handler`
    callable `(prompt, system, response_json) -> str` for input-dependent replies.
    Records every call on `.calls` so tests can assert on prompts.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        handler: Callable[[str, str | None, bool], str] | None = None,
    ):
        self._responses = list(responses or [])
        self._handler = handler
        self.calls: list[dict] = []

    def generate(
        self, prompt: str, *, system: str | None = None, response_json: bool = False
    ) -> str:
        self.calls.append({"prompt": prompt, "system": system, "response_json": response_json})
        if self._handler is not None:
            return self._handler(prompt, system, response_json)
        if self._responses:
            return self._responses.pop(0)
        return ""


def get_llm_client(model: str = GEMINI_FLASH_MODEL) -> LLMClient:
    """Return the real Gemini client (requires an API key).

    Unlike embeddings there's no meaningful offline default — classification and
    generation genuinely need the model — so callers that want offline behavior
    inject a `FakeLLMClient` explicitly.
    """
    return GeminiClient(model=model)
