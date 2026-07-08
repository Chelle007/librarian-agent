"""`librarian_handle` orchestrator — the Stage 2 LLM-driven entrypoint.

This is the single door for anything that originates as user free text (the MCP
`librarian_handle` tool). It wraps the deterministic Stage 1 `Librarian` (vault
writes + structured reads) with the LLM layer:

    classify (intent + mode)  →  route
      ├─ create → schema-validated write (via Librarian)
      ├─ query  → exact_lookup (template)  |  semantic/hybrid (RAG + groundedness)
      ├─ update → target resolution → write, gated by confidence
      └─ delete → target resolution → ALWAYS confirm → soft-delete

Keeping the deterministic core (`Librarian`) and this LLM router in separate
classes is deliberate: Stage 1 stays testable without any model, and this layer
is testable by injecting a `FakeLLMClient`. The return shape matches the MCP
contract (status / message / note_id / action) from the Architecture doc.
"""

from __future__ import annotations

from dataclasses import dataclass

from librarian.classifier import Classification, Classifier
from librarian.llm.gemini_client import LLMClient
from librarian.llm.rag import check_groundedness, generate_answer
from librarian.pipeline import Librarian
from librarian.retrieval.exact_lookup import ExactLookup
from librarian.retrieval.hybrid import HybridRetriever
from librarian.retrieval.semantic import SemanticRetriever
from librarian.target_resolution import resolve_target

_AFFIRMATIVES = frozenset(
    {"yes", "y", "confirm", "confirmed", "do it", "go ahead", "sure", "delete it", "yep", "yeah"}
)


@dataclass
class HandleResult:
    status: str  # "done" | "needs_clarification" | "error"
    message: str
    note_id: str | None = None
    action: str | None = None  # "created" | "updated" | "deleted" | "queried" | None


class LibrarianAgent:
    def __init__(self, librarian: Librarian, llm: LLMClient, *, use_prefilter: bool = False):
        # use_prefilter defaults OFF: the rule pre-filter can only emit a
        # fallback-type `note` (type detection needs the LLM), so firing it on a
        # typed create would mis-route a contact/task into notes/. Per the north
        # star (token efficiency is never traded against correctness), it's an
        # explicit opt-in, not the default.
        self.lib = librarian
        self.llm = llm
        self.classifier = Classifier(llm, librarian.schema, use_prefilter=use_prefilter)
        self.exact = ExactLookup(librarian.meta, librarian.vault)
        # Semantic/hybrid need the vector store; absent it, those paths error out
        # cleanly instead of pretending to search.
        self.semantic: SemanticRetriever | None = None
        self.hybrid: HybridRetriever | None = None
        if librarian.vector_store is not None and librarian.embedder is not None:
            self.semantic = SemanticRetriever(librarian.vector_store, librarian.embedder)
            self.hybrid = HybridRetriever(
                librarian.vector_store, librarian.embedder, librarian.meta
            )

    # ------------------------------------------------------------------- entry
    def handle(self, raw_request: str, context: str | None = None) -> HandleResult:
        c = self.classifier.classify(raw_request, context)

        if c.is_reaction:
            return self._reaction(c, raw_request, context)
        if c.intent == "create":
            return self._create(c, raw_request)
        if c.intent == "query":
            return self._query(c)
        if c.intent == "update":
            return self._update(c, context)
        if c.intent == "delete":
            return self._delete(c, raw_request, context)
        return HandleResult("error", "I couldn't understand that request.")

    # ------------------------------------------------------------------ create
    def _create(self, c: Classification, raw_request: str) -> HandleResult:
        fields = dict(c.fields)
        if c.tags:
            fields.setdefault("tags", c.tags)
        if c.links:
            fields.setdefault("links", c.links)

        note_type = c.note_type or self.lib.schema.fallback_type
        res = self.lib.create(
            type=note_type, fields=fields, body=c.body or "", raw_text=raw_request
        )
        if res.ok:
            return HandleResult("done", res.message, note_id=res.note_id, action="created")
        # Missing required fields is a solvable ambiguity, not a hard error — ask.
        if res.missing_required:
            needed = ", ".join(res.missing_required)
            return HandleResult(
                "needs_clarification",
                f"To save this {note_type} I still need: {needed}.",
            )
        return HandleResult("error", res.message)

    # ------------------------------------------------------------------- query
    def _query(self, c: Classification) -> HandleResult:
        mode = c.mode or "semantic"
        if mode == "exact_lookup":
            return self._query_exact(c)
        return self._query_rag(c, mode)

    def _query_exact(self, c: Classification) -> HandleResult:
        f = c.filters
        res = self.exact.lookup(
            type=f.get("type"),
            tag=f.get("tag"),
            keyword=f.get("keyword"),
            created_after=f.get("created_after"),
            created_before=f.get("created_before"),
            aggregate=bool(f.get("aggregate")),
        )
        note_id = res.hits[0].path if res.hits else None
        return HandleResult("done", res.message, note_id=note_id, action="queried")

    def _query_rag(self, c: Classification, mode: str) -> HandleResult:
        retriever = self.semantic if mode == "semantic" else self.hybrid
        if retriever is None:
            return HandleResult("error", "Semantic search is unavailable (vectors disabled).")

        query = c.query_text or ""
        if mode == "hybrid":
            f = c.filters
            hits = self.hybrid.search(
                query,
                type=f.get("type"),
                tag=f.get("tag"),
                created_after=f.get("created_after"),
                created_before=f.get("created_before"),
                k=5,
            )
        else:
            hits = self.semantic.search(query, k=5)

        if not hits:
            return HandleResult(
                "done", "I couldn't find anything in your vault about that.", action="queried"
            )

        answer = generate_answer(self.llm, query, hits)
        grounded = check_groundedness(self.llm, answer, hits)
        return HandleResult(
            "done", grounded.answer, note_id=hits[0].note_path, action="queried"
        )

    # ------------------------------------------------------------------ update
    def _update(self, c: Classification, context: str | None) -> HandleResult:
        if self.semantic is None:
            return HandleResult("error", "Target resolution is unavailable (vectors disabled).")

        tr = resolve_target(
            c.target_ref, meta=self.lib.meta, retriever=self.semantic, context=context
        )
        if not tr.resolved:
            return HandleResult("needs_clarification", _clarify_target("update", tr))

        res = self.lib.update(tr.path, fields=c.fields or None, body=c.body)
        if res.ok:
            return HandleResult("done", res.message, note_id=res.note_id, action="updated")
        if res.missing_required:
            needed = ", ".join(res.missing_required)
            return HandleResult("needs_clarification", f"That update would leave required fields empty: {needed}.")
        return HandleResult("error", res.message)

    # ------------------------------------------------------------------ delete
    def _delete(self, c: Classification, raw_request: str, context: str | None) -> HandleResult:
        if self.semantic is None:
            return HandleResult("error", "Target resolution is unavailable (vectors disabled).")

        tr = resolve_target(
            c.target_ref, meta=self.lib.meta, retriever=self.semantic, context=context
        )
        if not tr.resolved:
            return HandleResult("needs_clarification", _clarify_target("delete", tr))

        # Delete ALWAYS confirms, regardless of confidence — a destructive action
        # needs more friction than create/update. First pass asks; a follow-up
        # turn carrying an affirmative (folded into raw_request by the PA) proceeds.
        if not _is_affirmative(raw_request):
            return HandleResult(
                "needs_clarification",
                f"Delete {tr.path}? This can't be undone via chat — reply 'yes' to confirm.",
                note_id=tr.path,
            )

        res = self.lib.delete(tr.path)
        return HandleResult("done", res.message, note_id=res.note_id, action="deleted")

    # ---------------------------------------------------------------- reaction
    def _reaction(self, c: Classification, raw_request: str, context: str | None) -> HandleResult:
        """Explicit /correct_librarian trigger — log the correction signal."""
        self.lib.meta.log_correction(
            note_id=c.target_ref,
            original_classification=context,
            corrected_to=raw_request,
        )
        return HandleResult("done", "Got it — logged that correction.", action=None)


def _clarify_target(action: str, tr) -> str:
    if not tr.candidates:
        return f"I couldn't find a note matching that to {action}."
    listing = "; ".join(tr.candidates)
    return f"Which note should I {action}? I found several: {listing}."


def _is_affirmative(text: str) -> bool:
    t = (text or "").strip().lower().rstrip(".!")
    if t in _AFFIRMATIVES:
        return True
    return any(t.startswith(a + " ") or a in t.split() for a in _AFFIRMATIVES)
