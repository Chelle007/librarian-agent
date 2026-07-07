"""Write pipeline — the Stage 1 orchestrator (no LLM).

Ties the store components into the create/update/delete/query flows:

    build frontmatter -> schema validate -> route to folder -> archive raw
    -> write file -> upsert index -> git commit

In Stage 1 the caller (CLI harness / tests) supplies the note `type` and fields
explicitly — there is no classification yet. Stage 2 will sit an LLM classifier
in front of this and call the same methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from librarian.store.git_sync import GitSync
from librarian.store.metadata_store import MetadataStore
from librarian.store.schema import Schema
from librarian.store.vault_io import VaultIO


@dataclass
class WriteResult:
    status: str  # "done" | "error"
    action: str | None = None  # "created" | "updated" | "deleted" | "queried"
    note_id: str | None = None
    path: str | None = None
    message: str = ""
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "done"


class Librarian:
    """Stage 1 core: deterministic vault writes + structured reads."""

    def __init__(
        self,
        *,
        vault_root=None,
        db_path=None,
        schema_path=None,
        git_enabled: bool = False,
    ):
        self.vault = VaultIO(vault_root)
        self.schema = Schema.load(schema_path)
        self.meta = MetadataStore(db_path)
        self.git = GitSync(self.vault.root, enabled=git_enabled)

    # --------------------------------------------------------------- create
    def create(
        self,
        *,
        type: str,
        fields: dict | None = None,
        body: str = "",
        raw_text: str | None = None,
        slug: str | None = None,
    ) -> WriteResult:
        fields = dict(fields or {})

        # .raw/ write-through happens on every ingest, before any transform,
        # so the original input is never lost even if validation fails later.
        self.vault.archive_raw(raw_text if raw_text is not None else body)

        fm = {
            "type": type,
            "created_date": fields.pop("created_date", None) or _today(),
        }
        fm.update(fields)

        result = self.schema.validate_note(fm)
        if not result.is_valid:
            return WriteResult(
                status="error",
                message=f"missing required fields: {', '.join(result.missing_required)}",
                missing_required=result.missing_required,
            )

        # schema-on-read: an unknown type routes to the fallback bucket, but we
        # keep the intended type as a tag so the clustering pass can find it.
        if result.unknown_type:
            tags = list(fm.get("tags") or [])
            if type not in tags:
                tags.append(type)
            fm["tags"] = tags
        fm["type"] = result.resolved_type

        abs_path = self.vault.write(
            frontmatter=fm, body=body, folder=result.folder, slug=slug
        )
        rel = self.vault.relpath(abs_path)
        self.meta.upsert(
            path=rel,
            type=fm["type"],
            tags=fm.get("tags") or [],
            created_date=fm.get("created_date"),
        )
        self.git.commit(f"create {rel}")
        return WriteResult(
            status="done", action="created", note_id=rel, path=rel, message=f"created {rel}"
        )

    # --------------------------------------------------------------- update
    def update(
        self, path: str, *, fields: dict | None = None, body: str | None = None
    ) -> WriteResult:
        note = self.vault.read(path)  # raises VaultIOError if missing
        fm = dict(note.frontmatter)
        if fields:
            fm.update(fields)
        new_body = note.body if body is None else body

        result = self.schema.validate_note(fm)
        if not result.is_valid:
            return WriteResult(
                status="error",
                message=f"missing required fields: {', '.join(result.missing_required)}",
                missing_required=result.missing_required,
            )

        self.vault.overwrite(note.path, frontmatter=fm, body=new_body)
        rel = self.vault.relpath(note.path)
        self.meta.upsert(
            path=rel,
            type=fm.get("type"),
            tags=fm.get("tags") or [],
            created_date=fm.get("created_date"),
        )
        self.git.commit(f"update {rel}")
        return WriteResult(
            status="done", action="updated", note_id=rel, path=rel, message=f"updated {rel}"
        )

    # --------------------------------------------------------------- delete
    def delete(self, path: str) -> WriteResult:
        """Soft-delete: move to .trash/ and drop from the index. Never a hard rm."""
        rel = self.vault.relpath(path)
        dest = self.vault.soft_delete(path)
        self.meta.delete(rel)
        self.git.commit(f"delete {rel}")
        return WriteResult(
            status="done",
            action="deleted",
            note_id=rel,
            path=self.vault.relpath(dest),
            message=f"soft-deleted {rel}",
        )

    # ---------------------------------------------------------------- query
    def query_raw(self, **filters) -> list[dict]:
        """Structured, non-LLM read over the index (backs librarian_query_raw)."""
        return self.meta.query(**filters)

    # ------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self.meta.close()

    def __enter__(self) -> "Librarian":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _today() -> str:
    return date.today().isoformat()
