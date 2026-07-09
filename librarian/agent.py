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

from librarian.classifier import Classification, Classifier, _is_librarian_meta_correction
from librarian.link_resolution import (
    apply_links,
    format_mention_confirm,
    mentions_confirmed,
    wikilink_label,
)
from librarian.llm.context_gate import context_references_path
from librarian.llm.gemini_client import LLMClient
from librarian.llm.pending_update import (
    apply_pending_mutation,
    has_proposed_changes,
    is_pending_mutation_context,
    recover_pending_mutation,
    recover_pending_update,
)
from librarian.llm.rag import check_groundedness, generate_answer
from librarian.llm.update_check import check_update_conflict
from librarian.mention_search import find_mentions, identity_label
from librarian.note_preview import format_target_confirm
from librarian.pipeline import Librarian
from librarian.retrieval.exact_lookup import ExactLookup
from librarian.retrieval.hybrid import HybridRetriever
from librarian.retrieval.semantic import SemanticRetriever
from librarian.target_resolution import resolve_target
from librarian.write_resolution import WriteResolution, infer_note_type, resolve_write_target


@dataclass
class HandleResult:
    status: str  # "done" | "needs_clarification" | "error"
    message: str
    note_id: str | None = None
    action: str | None = None  # "created" | "updated" | "deleted" | "queried" | None


class LibrarianAgent:
    def __init__(self, librarian: Librarian, llm: LLMClient):
        self.lib = librarian
        self.llm = llm
        self.classifier = Classifier(llm, librarian.schema)
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
        if not c.actionable:
            return HandleResult("needs_clarification", c.clarify_message)

        if c.is_reaction and _is_librarian_meta_correction(raw_request):
            return self._reaction(c, raw_request, context)
        if c.intent in ("create", "update"):
            return self._mutate(c, raw_request, context)
        if c.intent == "query":
            return self._query(c)
        if c.intent == "delete":
            return self._delete(c, raw_request, context)
        return HandleResult("error", "I couldn't understand that request.")

    # ----------------------------------------------------------- create/update
    def _mutate(self, c: Classification, raw_request: str, context: str | None) -> HandleResult:
        c, recover_block = self._recover_mutation_if_needed(c, raw_request, context)
        if recover_block is not None:
            return recover_block

        if not c.fields and not c.body and c.intent == "update" and not c.is_confirmation:
            c.body = raw_request.strip()

        wr = resolve_write_target(
            c,
            raw_request,
            schema=self.lib.schema,
            meta=self.lib.meta,
            vault=self.lib.vault,
            retriever=self.semantic,
            context=context,
        )
        if wr.action == "clarify":
            return HandleResult(
                "needs_clarification",
                _clarify_write(wr, self.lib.vault),
                note_id=wr.path,
            )
        if wr.action == "update":
            assert wr.path is not None
            mention_gate = self._gate_mentions(c, raw_request, context, target_path=wr.path)
            if mention_gate is not None:
                return mention_gate
            link_paths = self._mention_link_paths(c, context, wr.path)
            return self._apply_update(c, wr.path, raw_request, context, link_paths=link_paths)

        mention_gate = self._gate_mentions(c, raw_request, context, target_path=None)
        if mention_gate is not None:
            return mention_gate
        return self._apply_create(
            c,
            raw_request,
            link_paths=self._mention_link_paths(c, context, None),
        )

    def _recover_mutation_if_needed(
        self,
        c: Classification,
        raw_request: str,
        context: str | None,
    ) -> tuple[Classification, HandleResult | None]:
        if not c.is_confirmation or not is_pending_mutation_context(context):
            return c, None
        if identity_label(c.fields) and has_proposed_changes(c, raw_request):
            return c, None

        pending = recover_pending_mutation(self.llm, context=context, request=raw_request)
        if not pending.sufficient:
            return c, HandleResult("needs_clarification", pending.message)
        return apply_pending_mutation(c, pending), None

    def _gate_mentions(
        self,
        c: Classification,
        raw_request: str,
        context: str | None,
        *,
        target_path: str | None,
    ) -> HandleResult | None:
        label = identity_label(c.fields)
        if not label:
            return None
        exclude = {target_path} if target_path else set()
        mentions = find_mentions(label, self.lib.meta, self.lib.vault, exclude=exclude)
        if target_path:
            note = self.lib.vault.read(target_path)
            linked = {str(x) for x in (note.frontmatter.get("links") or [])}
            mentions = [m for m in mentions if wikilink_label(m.path) not in linked]
        if not mentions:
            return None
        if c.is_confirmation and mentions_confirmed(context, mentions, label):
            return None
        action = "update" if target_path else "save"
        return HandleResult(
            "needs_clarification",
            format_mention_confirm(
                self.lib.vault, label, mentions, action=action, original_request=raw_request
            ),
            note_id=target_path,
        )

    def _mention_link_paths(
        self,
        c: Classification,
        context: str | None,
        target_path: str | None,
    ) -> list[str]:
        label = identity_label(c.fields)
        if not label or not c.is_confirmation:
            return []
        mentions = find_mentions(
            label,
            self.lib.meta,
            self.lib.vault,
            exclude={target_path} if target_path else set(),
        )
        if not mentions_confirmed(context, mentions, label):
            return []
        return [m.path for m in mentions]

    def _apply_create(
        self, c: Classification, raw_request: str, *, link_paths: list[str] | None = None
    ) -> HandleResult:
        fields = dict(c.fields)
        if c.tags:
            fields.setdefault("tags", c.tags)
        fields = apply_links(
            fields,
            existing=None,
            paths=link_paths or [],
            extra=c.links,
        )

        note_type = (
            c.note_type
            or infer_note_type(self.lib.schema, fields, c.note_type)
            or self.lib.schema.fallback_type
        )
        if (
            note_type == self.lib.schema.fallback_type
            and not identity_label(fields)
            and (not c.body or c.body.strip() == raw_request.strip())
        ):
            return HandleResult(
                "needs_clarification",
                "I need something to save — tell me the fact you want in your vault.",
            )

        res = self.lib.create(
            type=note_type, fields=fields, body=c.body or "", raw_text=raw_request
        )
        if res.ok:
            return HandleResult("done", res.message, note_id=res.note_id, action="created")
        if res.missing_required:
            needed = ", ".join(res.missing_required)
            return HandleResult(
                "needs_clarification",
                f"To save this {note_type} I still need: {needed}.",
            )
        return HandleResult("error", res.message)

    def _apply_update(
        self,
        c: Classification,
        path: str,
        raw_request: str,
        context: str | None,
        *,
        link_paths: list[str] | None = None,
    ) -> HandleResult:
        note = self.lib.vault.read(path)
        body = c.body
        confirming = _confirms_pending_action(c, context, path)

        if confirming and not has_proposed_changes(c, raw_request):
            pending = recover_pending_update(
                self.llm,
                context=context,
                path=path,
                existing_frontmatter=note.frontmatter,
                existing_body=note.body,
                request=raw_request,
            )
            if not pending.sufficient:
                return HandleResult("needs_clarification", pending.message, note_id=path)
            if pending.fields:
                merged = dict(c.fields)
                merged.update(pending.fields)
                c.fields = merged
            if pending.body:
                c.body = pending.body
            body = c.body

        if body and note.body and body.strip() not in note.body:
            body = note.body.rstrip() + "\n\n" + body.strip()

        if not confirming:
            conflict = check_update_conflict(
                self.llm,
                existing_frontmatter=note.frontmatter,
                existing_body=note.body,
                proposed_fields=c.fields or None,
                proposed_body=body,
                request=raw_request,
            )
            if conflict.has_conflict:
                return HandleResult(
                    "needs_clarification",
                    f"{conflict.message} Confirm if you want to update anyway.",
                    note_id=path,
                )

        fields = apply_links(
            dict(c.fields),
            existing=note.frontmatter,
            paths=link_paths or [],
            extra=c.links,
        )
        res = self.lib.update(path, fields=fields or None, body=body)
        if res.ok:
            return HandleResult("done", res.message, note_id=res.note_id, action="updated")
        if res.missing_required:
            needed = ", ".join(res.missing_required)
            return HandleResult(
                "needs_clarification",
                f"That update would leave required fields empty: {needed}.",
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

    # ------------------------------------------------------------------ delete
    def _delete(self, c: Classification, raw_request: str, context: str | None) -> HandleResult:
        if self.semantic is None:
            return HandleResult("error", "Target resolution is unavailable (vectors disabled).")

        tr = resolve_target(
            c.target_ref, meta=self.lib.meta, retriever=self.semantic, context=context
        )
        if not tr.resolved:
            return HandleResult(
                "needs_clarification",
                _clarify_target("delete", tr, self.lib.vault),
                note_id=tr.path,
            )

        if not c.is_confirmation or not context_references_path(context, tr.path):
            return HandleResult(
                "needs_clarification",
                f"Delete {tr.path}? This can't be undone via chat — confirm to proceed.",
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


def _clarify_write(wr: WriteResolution, vault) -> str:
    if not wr.candidates:
        return "I couldn't find a note to update."
    if wr.path:
        return format_target_confirm(vault, wr.path, action="update")
    listing = "; ".join(wr.candidates)
    return f"Which note should I update? I found several: {listing}."


def _clarify_target(action: str, tr, vault) -> str:
    if not tr.candidates:
        return f"I couldn't find a note matching that to {action}."
    if tr.path:
        return format_target_confirm(vault, tr.path, action=action)
    listing = "; ".join(tr.candidates)
    return f"Which note should I {action}? I found several: {listing}."


def _confirms_pending_action(c: Classification, context: str | None, path: str) -> bool:
    """Skip conflict check when context shows the user confirmed this update."""
    return c.is_confirmation and context_references_path(context, path)
