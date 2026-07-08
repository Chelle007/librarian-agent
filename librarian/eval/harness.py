"""Auto-generated retrieval eval harness (recall@k / MRR).

The pattern (see Architecture, "Eval Harness"): rather than hand-write a fixed
15-20 case test set, bootstrap one from the vault itself —

1. sample N notes,
2. ask a generator to write a question each note answers *without reusing its
   title words* (forces semantic retrieval, not string matching against titles),
3. treat the sampled note as the gold answer,
4. run the retrieval pipeline on each question and score whether the gold note
   lands in the top-k.

The test set grows with the vault and doubles as portfolio material. The
question `generator` is pluggable behind a Protocol so the harness runs fully
offline in tests (a deterministic keyword generator) or against Gemini Flash in
production — and the scoring is pure arithmetic on already-retrieved hits, so
it's deterministic regardless of which embedder/retriever is under test.

The `retriever` is anything with `.search(query, k) -> list[hit]` where each hit
has a `.note_path` (both `SemanticRetriever` and `HybridRetriever` qualify).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from librarian.ingestion.embed_text import note_embed_text
from librarian.store.vault_io import VaultIO

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tiny stoplist — enough to keep the offline generator's questions on-topic.
_STOPWORDS: frozenset[str] = frozenset(
    """the a an and or but of to in on at for with from by is are was were be been
    this that these those it its as into about over under how what when where why
    who which my your their his her our i you he she they we me him them us""".split()
)


@dataclass
class EvalCase:
    note_path: str  # the gold answer
    question: str


@dataclass
class EvalReport:
    k: int
    total: int
    hits_at_k: int
    recall_at_k: float
    mrr: float
    per_case: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"recall@{self.k}={self.recall_at_k:.2%} "
            f"({self.hits_at_k}/{self.total}), MRR={self.mrr:.3f}"
        )


@runtime_checkable
class QuestionGenerator(Protocol):
    def generate(self, note_path: str, frontmatter: dict, body: str) -> str: ...


class KeywordQuestionGenerator:
    """Offline, deterministic generator — no API needed.

    Picks salient content tokens from the note, *excluding* its title/name words
    (honoring the "don't reuse title words" rule), and phrases a plain question.
    Not natural-sounding, but repeatable and dependency-free — enough to exercise
    and regression-test the scoring pipeline without the network.
    """

    def __init__(self, max_terms: int = 8):
        self.max_terms = max_terms

    def generate(self, note_path: str, frontmatter: dict, body: str) -> str:
        title = str(frontmatter.get("title") or frontmatter.get("name") or "")
        title_tokens = set(_TOKEN_RE.findall(title.lower()))

        terms: list[str] = []
        seen: set[str] = set()
        for tok in _TOKEN_RE.findall(note_embed_text(frontmatter, body).lower()):
            if tok in title_tokens or tok in _STOPWORDS or tok in seen or len(tok) < 3:
                continue
            seen.add(tok)
            terms.append(tok)
            if len(terms) >= self.max_terms:
                break
        return "what is this about: " + " ".join(terms)


class GeminiQuestionGenerator:
    """Production generator — Gemini Flash writes a question per note.

    Lazy-imports `google-genai` so offline installs/tests never need it.
    """

    _PROMPT = (
        "Write ONE natural question that the following note answers. "
        "Do NOT reuse words from the note's title. Return only the question.\n\n"
        "Title: {title}\n\n{content}"
    )

    def __init__(self, api_key: str | None = None, model: str = "gemini-flash-latest"):
        try:
            from google import genai  # noqa: PLC0415 (lazy by design)
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "google-genai is required for GeminiQuestionGenerator "
                "(`pip install google-genai`), or use KeywordQuestionGenerator offline"
            ) from exc

        import os  # noqa: PLC0415

        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def generate(self, note_path: str, frontmatter: dict, body: str) -> str:  # pragma: no cover - network
        title = str(frontmatter.get("title") or frontmatter.get("name") or "")
        prompt = self._PROMPT.format(title=title, content=note_embed_text(frontmatter, body))
        resp = self._client.models.generate_content(model=self.model, contents=prompt)
        return (resp.text or "").strip()


def build_eval_set(
    vault: VaultIO,
    generator: QuestionGenerator,
    *,
    n: int | None = None,
    seed: int = 0,
    types: set[str] | None = None,
) -> list[EvalCase]:
    """Sample notes and generate one question each. `n=None` uses every note."""
    notes = list(vault.iter_notes())
    if types is not None:
        notes = [nt for nt in notes if nt.frontmatter.get("type") in types]

    if n is not None and n < len(notes):
        notes = random.Random(seed).sample(notes, n)

    cases: list[EvalCase] = []
    for note in notes:
        rel = vault.relpath(note.path)
        question = generator.generate(rel, note.frontmatter, note.body)
        if question.strip():
            cases.append(EvalCase(note_path=rel, question=question))
    return cases


def score(retriever, cases: list[EvalCase], *, k: int = 5) -> EvalReport:
    """Run each question through `retriever` and score recall@k + MRR on the gold note."""
    per_case: list[dict] = []
    hits_at_k = 0
    reciprocal_sum = 0.0

    for case in cases:
        results = retriever.search(case.question, k=k)
        paths = [h.note_path for h in results]
        rank = paths.index(case.note_path) + 1 if case.note_path in paths else None
        is_hit = rank is not None and rank <= k
        if is_hit:
            hits_at_k += 1
            reciprocal_sum += 1.0 / rank
        per_case.append(
            {"note_path": case.note_path, "question": case.question, "rank": rank, "hit": is_hit}
        )

    total = len(cases)
    return EvalReport(
        k=k,
        total=total,
        hits_at_k=hits_at_k,
        recall_at_k=(hits_at_k / total) if total else 0.0,
        mrr=(reciprocal_sum / total) if total else 0.0,
        per_case=per_case,
    )
