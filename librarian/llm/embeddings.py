"""Embedding helper — the single place embeddings are produced.

Locked decisions (see `docs/Embedding & Chunking Decision (Jul 7).md`):
- Model: `gemini-embedding-2` (multimodal, unified text+image+... space, 8192-token
  input), output truncated to 768 dims (Matryoshka). Chosen over `-001` so future
  image embeddings live in the SAME vector space as text (cross-modal retrieval).
- Asymmetric retrieval is expressed differently per model:
  * `-2` has no `task_type` field — the task is a text PREFIX on the input
    (`task: search result | query: …` for queries, `title: … | text: …` for docs).
  * `-001` uses the `task_type` enum (RETRIEVAL_QUERY / RETRIEVAL_DOCUMENT).
  Both are supported here so the eval harness can A/B them; the vault picks one.
- L2-normalize every vector centrally (no call site can forget). `-2` auto-normalizes
  truncated dims and `-001` does not — normalizing a unit vector is a harmless no-op,
  so doing it unconditionally keeps the two models interchangeable.

Two implementations behind one `Embedder` interface:
- `GeminiEmbedder` — the real thing (needs an API key + network).
- `HashingEmbedder` — deterministic, offline, no dependencies. NOT semantically
  meaningful (it's a hashed bag-of-words), but same-dim and repeatable, so the
  whole retrieval stack is testable and locally runnable without the API. Word
  overlap → similarity, which is enough to exercise ranking logic in tests.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol, runtime_checkable

EMBED_MODEL = "gemini-embedding-2"
EMBED_DIM = 768  # Matryoshka-truncated (recommended: 768 / 1536 / 3072)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def l2_normalize(vec: list[float]) -> list[float]:
    """Scale a vector to unit length. Required for truncated Gemini embeddings."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Offline, deterministic embedder for tests + no-API local dev.

    Hashes tokens into `dim` buckets with signed accumulation, then L2-normalizes.
    Shared vocabulary between two texts yields high cosine similarity — enough to
    verify retrieval ranking without any network call. Not for production quality.
    """

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        return l2_normalize(vec)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class GeminiEmbedder:
    """Production embedder: Gemini API, 768-d, L2-normalized.

    Handles both model generations transparently:
    - `gemini-embedding-2`: asymmetric retrieval via text prefixes (no task_type).
    - `gemini-embedding-001`: asymmetric retrieval via the `task_type` enum.

    `google-genai` is imported lazily so tests / installs that never touch the
    real API don't need the dependency present.
    """

    # Retrieval task label used in the `-2` query prefix.
    _V2_QUERY_TASK = "search result"

    def __init__(
        self,
        api_key: str | None = None,
        dim: int = EMBED_DIM,
        model: str = EMBED_MODEL,
    ):
        try:
            from google import genai  # noqa: PLC0415 (lazy by design)
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "google-genai is required for GeminiEmbedder "
                "(`pip install google-genai`), or use HashingEmbedder for offline dev"
            ) from exc

        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.dim = dim
        self.model = model
        self._is_v2 = "gemini-embedding-2" in model  # prefix mode vs task_type mode

    def _embed_raw(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        from google.genai import types  # noqa: PLC0415

        config_kwargs: dict = {"output_dimensionality": self.dim}
        if task_type is not None:  # only -001 accepts task_type
            config_kwargs["task_type"] = task_type

        resp = self._client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(**config_kwargs),
        )
        # Normalize centrally: required for truncated -001, harmless no-op for -2.
        return [l2_normalize(list(e.values)) for e in resp.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # gemini-embedding-2 returns one vector per embed_content call even when
        # multiple contents are passed — embed sequentially for a 1:1 mapping.
        out: list[list[float]] = []
        for t in texts:
            if self._is_v2:
                out.append(self._embed_raw([f"title: none | text: {t}"])[0])
            else:
                out.append(self._embed_raw([t], task_type="RETRIEVAL_DOCUMENT")[0])
        return out

    def embed_query(self, text: str) -> list[float]:
        if self._is_v2:
            return self._embed_raw([f"task: {self._V2_QUERY_TASK} | query: {text}"])[0]
        return self._embed_raw([text], task_type="RETRIEVAL_QUERY")[0]


def get_embedder(kind: str | None = None, *, dim: int = EMBED_DIM) -> Embedder:
    """Pick an embedder.

    `kind`: "gemini" | "hashing" | None. When None, use env `LIBRARIAN_EMBEDDER`
    if set, else auto-detect: Gemini when an API key is present, otherwise the
    offline HashingEmbedder.
    """
    kind = kind or os.environ.get("LIBRARIAN_EMBEDDER")
    if kind == "hashing":
        return HashingEmbedder(dim=dim)
    if kind == "gemini":
        return GeminiEmbedder(dim=dim)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return GeminiEmbedder(dim=dim)
    return HashingEmbedder(dim=dim)
