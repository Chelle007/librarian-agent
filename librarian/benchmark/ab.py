"""Token A/B benchmark — Arm A (full custom Librarian) vs Arm B (passthrough).

Quantifies the payoff of the two-agent split (see Architecture, "Planned
Experiment: Token A/B Benchmark"):

- **Arm A** routes each request through the full `LibrarianAgent`: one
  classification call, then structured retrieval (SQLite, no tokens) or
  RAG + groundedness. Cost concentrates in classification + generation.
- **Arm B** models the "passthrough Librarian" — hand the raw request plus the
  vault's content to a single LLM call and let it reason in-context (as a direct
  Obsidian-MCP setup would). Cost scales with how much vault content is stuffed
  into the prompt.

Hypothesis: Arm B loses most on structured/keyword/aggregation queries (Arm A
answers those from SQLite with *zero* generation tokens), and the gap narrows on
semantic queries where both need a generation call.

Arm B here is a faithful *token-cost model*, not a full read/write agent — it
issues the in-context call and measures it, but doesn't mutate the vault. Real
numbers need a live Gemini run; offline (FakeLLMClient) it still exercises the
harness and produces chars/4 estimates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from librarian.agent import LibrarianAgent
from librarian.benchmark.tokens import MeteredLLMClient, TokenTracker
from librarian.ingestion.embed_text import note_embed_text
from librarian.llm.gemini_client import LLMClient
from librarian.pipeline import Librarian


@dataclass(frozen=True)
class BenchRequest:
    id: str
    raw: str
    kind: str  # create | update | delete | exact_lookup | semantic | hybrid
    context: str | None = None


@dataclass
class ArmResult:
    request_id: str
    kind: str
    arm: str  # "A" | "B"
    status: str
    action: str | None
    total_tokens: int
    calls: int
    by_phase: dict[str, int] = field(default_factory=dict)


# A representative spread across every path + create/update/delete.
DEFAULT_REQUESTS: tuple[BenchRequest, ...] = (
    BenchRequest("c1", "idea: batch the embed calls to cut ingestion latency", "create"),
    BenchRequest("c2", "save my friend Priya, birthday 1999-03-12, she likes climbing", "create"),
    BenchRequest("c3", "task: file quarterly taxes, due 2026-07-30", "create"),
    BenchRequest("u1", "Priya also likes film photography", "update", context="talking about Priya"),
    BenchRequest("q1", "how many contacts do I have?", "exact_lookup"),
    BenchRequest("q2", "find my note about batching embed calls", "exact_lookup"),
    BenchRequest("q3", "what was my idea for cutting ingestion latency?", "semantic"),
    BenchRequest("q4", "any tasks about taxes due this month?", "hybrid"),
    BenchRequest("q5", "summarize what I know about Priya", "semantic"),
    BenchRequest("d1", "delete the note about batching embed calls", "delete"),
)


def seed_demo_vault(lib: Librarian) -> None:
    """Populate a scratch vault so queries have prior content to hit."""
    lib.create(type="note", body="Reading list: Dune, Project Hail Mary", raw_text="x")
    lib.create(type="contact", fields={"name": "Sam", "likes": "board games"})
    lib.create(type="habit", fields={"name": "Stretch", "frequency": "daily"})


# ------------------------------------------------------------------- runners
def run_arm_a(lib: Librarian, llm: LLMClient, requests) -> list[ArmResult]:
    """Full custom pipeline: classify → structured/RAG, metered per phase."""
    tracker = TokenTracker()
    agent = LibrarianAgent(lib, MeteredLLMClient(llm, tracker))
    results: list[ArmResult] = []
    for req in requests:
        tracker.reset()
        res = agent.handle(req.raw, req.context)
        results.append(
            ArmResult(
                req.id, req.kind, "A", res.status, res.action,
                tracker.total_tokens, tracker.calls, tracker.by_phase(),
            )
        )
    return results


def run_arm_b(lib: Librarian, llm: LLMClient, requests) -> list[ArmResult]:
    """Passthrough model: one in-context LLM call over the whole vault per request."""
    tracker = TokenTracker()
    metered = MeteredLLMClient(llm, tracker)
    results: list[ArmResult] = []
    for req in requests:
        vault_dump = _dump_vault(lib)
        prompt = _PASSTHROUGH_PROMPT.format(
            vault=vault_dump, context=req.context or "(none)", request=req.raw
        )
        tracker.reset()
        metered.generate(prompt, system=_PASSTHROUGH_SYSTEM)
        results.append(
            ArmResult(
                req.id, req.kind, "B", "done", None,
                tracker.total_tokens, tracker.calls, tracker.by_phase(),
            )
        )
    return results


def run_benchmark(
    lib: Librarian, llm: LLMClient, *, requests=None, arms=("A", "B")
) -> "BenchmarkReport":
    requests = list(requests or DEFAULT_REQUESTS)
    results: list[ArmResult] = []
    if "A" in arms:
        results += run_arm_a(lib, llm, requests)
    if "B" in arms:
        results += run_arm_b(lib, llm, requests)
    return BenchmarkReport(results=results)


def _dump_vault(lib: Librarian, max_notes: int = 200) -> str:
    """Concatenate note content the way a passthrough LLM would have to read it."""
    parts: list[str] = []
    for note in lib.vault.iter_notes():
        rel = lib.vault.relpath(note.path)
        parts.append(f"### {rel}\n{note_embed_text(note.frontmatter, note.body)}")
        if len(parts) >= max_notes:
            break
    return "\n\n".join(parts)


# -------------------------------------------------------------------- report
@dataclass
class BenchmarkReport:
    results: list[ArmResult]

    def _arm(self, arm: str) -> list[ArmResult]:
        return [r for r in self.results if r.arm == arm]

    def totals_by_arm(self) -> dict[str, int]:
        return {arm: sum(r.total_tokens for r in self._arm(arm)) for arm in ("A", "B")}

    def phase_totals_a(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._arm("A"):
            for phase, toks in r.by_phase.items():
                out[phase] = out.get(phase, 0) + toks
        return out

    def by_kind(self) -> dict[str, dict[str, float]]:
        kinds: dict[str, dict[str, list[int]]] = {}
        for r in self.results:
            kinds.setdefault(r.kind, {"A": [], "B": []})[r.arm].append(r.total_tokens)
        summary: dict[str, dict[str, float]] = {}
        for kind, arms in kinds.items():
            summary[kind] = {
                arm: (sum(v) / len(v) if v else 0.0) for arm, v in arms.items()
            }
        return summary

    def format_text(self) -> str:
        lines = ["Token A/B Benchmark", "=" * 60]

        by_id: dict[str, dict[str, ArmResult]] = {}
        for r in self.results:
            by_id.setdefault(r.request_id, {})[r.arm] = r

        lines.append(f"{'id':<5}{'kind':<14}{'A tok':>8}{'B tok':>8}{'B/A':>7}")
        for rid, arms in by_id.items():
            a = arms.get("A")
            b = arms.get("B")
            a_tok = a.total_tokens if a else 0
            b_tok = b.total_tokens if b else 0
            ratio = f"{b_tok / a_tok:.1f}x" if a_tok else "-"
            kind = (a or b).kind
            lines.append(f"{rid:<5}{kind:<14}{a_tok:>8}{b_tok:>8}{ratio:>7}")

        totals = self.totals_by_arm()
        lines.append("-" * 60)
        ratio = f"{totals['B'] / totals['A']:.2f}x" if totals["A"] else "-"
        lines.append(f"TOTAL Arm A={totals['A']}  Arm B={totals['B']}  (B/A = {ratio})")

        phases = self.phase_totals_a()
        if phases:
            phase_str = ", ".join(f"{p}={t}" for p, t in sorted(phases.items()))
            lines.append(f"Arm A token concentration: {phase_str}")

        lines.append("")
        lines.append("Avg tokens by kind:")
        lines.append(f"  {'kind':<14}{'A':>8}{'B':>8}")
        for kind, arms in self.by_kind().items():
            lines.append(f"  {kind:<14}{arms['A']:>8.0f}{arms['B']:>8.0f}")
        return "\n".join(lines)


_PASSTHROUGH_SYSTEM = (
    "You are a personal knowledge assistant with direct access to the user's "
    "vault. Read the provided vault content and satisfy the request."
)
_PASSTHROUGH_PROMPT = """\
Vault contents:
{vault}

Recent conversation: {context}

User request: {request}

Answer or perform the request using the vault contents above.
"""
