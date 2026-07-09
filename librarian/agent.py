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
contract (status / message / note_id / action / pending_id) from the Architecture doc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from librarian.classifier import Classification, Classifier
from librarian.link_resolution import apply_links, format_mention_confirm, wikilink_label
from librarian.mention_search import find_mentions, identity_label
from librarian.note_preview import format_target_confirm
from librarian.llm.gemini_client import LLMClient
from librarian.llm.rag import check_groundedness, generate_answer
from librarian.llm.update_check import check_update_conflict
from librarian.pending_confirm import (
    DEFAULT_TTL_SECONDS,
    DELETE_TTL_SECONDS,
    classification_from_pending,
)
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
    pending_id: str | None = None


class LibrarianAgent:
    def __init__(self, librarian: Librarian, llm: LLMClient):
        self.lib = librarian
        self.llm = llm
        self.classifier = Classifier(llm, librarian.schema)
        self.exact = ExactLookup(librarian.meta, librarian.vault)
        self.semantic: SemanticRetriever | None = None
        self.hybrid: HybridRetriever | None = None
        if librarian.vector_store is not None and librarian.embedder is not None:
            self.semantic = SemanticRetriever(librarian.vector_store, librarian.embedder)
            self.hybrid = HybridRetriever(
                librarian.vector_store, librarian.embedder, librarian.meta
            )

    # ------------------------------------------------------------------- entry
    def handle(
        self,
        raw_request: str,
        context: str | None = None,
        *,
        pending_id: str | None = None,
        approved: bool | None = None,
    ) -> HandleResult:
        if pending_id is not None:
            if approved is None:
                return HandleResult(
                    "error",
                    "pending_id requires approved=true or approved=false.",
                )
            return self.handle_confirm(pending_id, approved=approved)

        c = self.classifier.classify(raw_request, context)
        if not c.actionable:
            return HandleResult("needs_clarification", c.clarify_message)

        if c.intent in ("create", "update"):
            return self._mutate(c, raw_request, context)
        if c.intent == "query":
            return self._query(c)
        if c.intent == "delete":
            return self._delete(c, raw_request, context)
        return HandleResult("error", "I couldn't understand that request.")

    def handle_confirm(self, pending_id: str, *, approved: bool) -> HandleResult:
        """Execute or cancel a stored pending confirmation (also via handle(pending_id=…))."""
        row = self.lib.meta.get_pending(pending_id)
        if row is None:
            return HandleResult(
                "needs_clarification",
                "That confirmation expired or was not found. Say it again if you still want that.",
            )
        if not approved:
            self.lib.meta.settle_pending(pending_id, "rejected")
            return HandleResult("done", "Cancelled.", action=None)

        self.lib.meta.settle_pending(pending_id, "approved")
        c = classification_from_pending(row)
        raw_request = row["raw_request"]
        link_paths = row["link_paths"]

        if row["intent"] == "delete":
            path = row["target_path"]
            assert path
            res = self.lib.delete(path)
            return HandleResult("done", res.message, note_id=res.note_id, action="deleted")
        if row["intent"] == "update":
            assert row["target_path"]
            return self._apply_update(
                c,
                row["target_path"],
                raw_request,
                link_paths=link_paths,
                skip_conflict=True,
                pending_kind=row["kind"],
            )
        if row["intent"] == "create":
            return self._apply_create(c, raw_request, link_paths=link_paths)
        return HandleResult("error", "Unknown pending intent.")

    # ----------------------------------------------------------- create/update
    def _mutate(self, c: Classification, raw_request: str, context: str | None) -> HandleResult:
        if not c.fields and not c.body and c.intent == "update":
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
            mention_gate = self._gate_mentions(c, raw_request, target_path=wr.path)
            if mention_gate is not None:
                return mention_gate
            return self._apply_update(c, wr.path, raw_request)

        mention_gate = self._gate_mentions(c, raw_request, target_path=None)
        if mention_gate is not None:
            return mention_gate
        return self._apply_create(c, raw_request)

    def _gate_mentions(
        self,
        c: Classification,
        raw_request: str,
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

        action = "update" if target_path else "save"
        message = format_mention_confirm(
            self.lib.vault, label, mentions, action=action, original_request=raw_request
        )
        return self._pending(
            kind="mention",
            intent="update" if target_path else "create",
            message=message,
            c=c,
            raw_request=raw_request,
            target_path=target_path,
            link_paths=[m.path for m in mentions],
        )

    def _pending(
        self,
        *,
        kind: str,
        intent: str,
        message: str,
        c: Classification,
        raw_request: str,
        target_path: str | None = None,
        link_paths: list[str] | None = None,
        body: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> HandleResult:
        pending_id = self.lib.meta.create_pending(
            kind=kind,
            intent=intent,
            message=message,
            raw_request=raw_request,
            note_type=c.note_type,
            target_path=target_path,
            fields=c.fields,
            body=body if body is not None else c.body,
            link_paths=link_paths,
            tags=c.tags,
            links=c.links,
            ttl_seconds=ttl_seconds,
        )
        return HandleResult(
            "needs_clarification",
            message,
            note_id=target_path,
            pending_id=pending_id,
        )

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
        *,
        link_paths: list[str] | None = None,
        skip_conflict: bool = False,
        pending_kind: str | None = None,
    ) -> HandleResult:
        note = self.lib.vault.read(path)
        body = c.body
        if body and note.body and body.strip() not in note.body:
            body = note.body.rstrip() + "\n\n" + body.strip()

        if not skip_conflict:
            conflict = check_update_conflict(
                self.llm,
                existing_frontmatter=note.frontmatter,
                existing_body=note.body,
                proposed_fields=c.fields or None,
                proposed_body=body,
                request=raw_request,
            )
            if conflict.has_conflict:
                message = f"{conflict.message} Confirm if you want to update anyway."
                return self._pending(
                    kind="conflict",
                    intent="update",
                    message=message,
                    c=c,
                    raw_request=raw_request,
                    target_path=path,
                    link_paths=link_paths,
                    body=body,
                )

        fields = apply_links(
            dict(c.fields),
            existing=note.frontmatter,
            paths=link_paths or [],
            extra=c.links,
        )
        res = self.lib.update(path, fields=fields or None, body=body)
        if res.ok:
            if _should_log_correction(c, pending_kind=pending_kind):
                self._log_correction(
                    note_id=path,
                    frontmatter=note.frontmatter,
                    body=note.body,
                    corrected_to=raw_request,
                )
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

        message = f"Delete {tr.path}? This can't be undone via chat — confirm to proceed."
        return self._pending(
            kind="delete",
            intent="delete",
            message=message,
            c=c,
            raw_request=raw_request,
            target_path=tr.path,
            ttl_seconds=DELETE_TTL_SECONDS,
        )

    def _log_correction(
        self, *, note_id: str, frontmatter: dict, body: str, corrected_to: str
    ) -> None:
        self.lib.meta.log_correction(
            note_id=note_id,
            original_classification=_note_snapshot(frontmatter, body),
            corrected_to=corrected_to,
        )


def _should_log_correction(c: Classification, *, pending_kind: str | None = None) -> bool:
    return c.is_reaction or pending_kind == "conflict"


def _note_snapshot(frontmatter: dict, body: str) -> str:
    return json.dumps({"frontmatter": frontmatter, "body": body}, ensure_ascii=False)


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
