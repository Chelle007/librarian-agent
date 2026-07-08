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
from datetime import date, datetime, timezone
from pathlib import Path

from librarian.ingestion.chunker import chunk_note
from librarian.ingestion.embed_text import note_embed_text
from librarian.llm.embeddings import Embedder, get_embedder
from librarian.store.git_sync import GitSync
from librarian.store.metadata_store import MetadataStore
from librarian.store.schema import Schema
from librarian.store.vault_io import VaultIO
from librarian.store.vector_store import DEFAULT_VECTOR_DB_PATH, VectorStore


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
        vector_enabled: bool = True,
        vector_db_path=None,
        embedder: Embedder | None = None,
    ):
        self.vault = VaultIO(vault_root)
        self.schema = Schema.load(schema_path)
        self.meta = MetadataStore(db_path)
        self.git = GitSync(self.vault.root, enabled=git_enabled)

        # Vector indexing is a derived cache, like the metadata store: every write
        # keeps it in lockstep so semantic/hybrid retrieval sees fresh content.
        # It degrades gracefully — with no Gemini key, get_embedder() returns the
        # offline HashingEmbedder, so the pipeline stays runnable (and testable)
        # without the API. Set vector_enabled=False to skip it entirely.
        self.embedder: Embedder | None = None
        self.vector_store: VectorStore | None = None
        if vector_enabled:
            self.embedder = embedder or get_embedder()
            self.vector_store = VectorStore(
                _resolve_vector_db_path(db_path, vector_db_path),
                dim=self.embedder.dim,
            )

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
        self._index_vectors(rel, fm["type"], fm, body)
        self.git.commit_and_push(f"create {rel}")
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
        self._index_vectors(rel, self.schema.resolve_type(fm.get("type")), fm, new_body)
        self.git.commit_and_push(f"update {rel}")
        return WriteResult(
            status="done", action="updated", note_id=rel, path=rel, message=f"updated {rel}"
        )

    # --------------------------------------------------------------- delete
    def delete(self, path: str) -> WriteResult:
        """Soft-delete: move to .trash/ and drop from the index. Never a hard rm."""
        rel = self.vault.relpath(path)
        dest = self.vault.soft_delete(path)
        self.meta.delete(rel)
        if self.vector_store is not None:
            self.vector_store.delete_note(rel)
        self.git.commit_and_push(f"delete {rel}")
        return WriteResult(
            status="done",
            action="deleted",
            note_id=rel,
            path=self.vault.relpath(dest),
            message=f"soft-deleted {rel}",
        )

    # ------------------------------------------------------------- vectors
    def _index_vectors(self, rel: str, note_type: str, frontmatter: dict, body: str) -> None:
        """Embed a note and (re)write its chunks in the vector store.

        No-op when vectors are disabled. Delete-then-insert inside `index_note`
        keeps this idempotent, so it doubles as the update path (stale chunks from
        the old content never linger).
        """
        if self.vector_store is None or self.embedder is None:
            return
        text = note_embed_text(frontmatter, body)
        chunks = chunk_note(note_type, text)
        embeddings = self.embedder.embed_documents([c.text for c in chunks])
        self.vector_store.index_note(rel, chunks, embeddings)

    # ---------------------------------------------------------------- query
    def query_raw(self, **filters) -> list[dict]:
        """Structured, non-LLM read over the index (backs librarian_query_raw)."""
        return self.meta.query(**filters)

    # -------------------------------------------------------------- reindex
    def reindex(self) -> int:
        """Rebuild the metadata index from the vault markdown (source of truth).

        The index is a derived cache; a git pull (e.g. desktop Obsidian edits)
        can add, change, or remove notes it never saw. This walks every content
        note and replaces the `notes` table wholesale. Returns the note count.
        No LLM, fully deterministic.
        """
        if self.vector_store is not None:
            self.vector_store.clear()

        rows: list[dict] = []
        for note in self.vault.iter_notes():
            fm = note.frontmatter
            rel = self.vault.relpath(note.path)
            resolved_type = self.schema.resolve_type(fm.get("type"))
            rows.append(
                {
                    "path": rel,
                    "type": resolved_type,
                    "tags": _as_tag_list(fm.get("tags")),
                    "created_date": _as_date_str(fm.get("created_date")),
                    "last_modified": _mtime_iso(note.path),
                }
            )
            # Rebuild vectors from the same source-of-truth walk, so the metadata
            # and vector indexes never drift out of sync after a reconcile.
            self._index_vectors(rel, resolved_type, fm, note.body)
        self.meta.replace_notes(rows)
        return len(rows)

    # ------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self.meta.close()
        if self.vector_store is not None:
            self.vector_store.close()

    def __enter__(self) -> "Librarian":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _resolve_vector_db_path(db_path, vector_db_path):
    """Pick where the vector DB lives, mirroring the metadata DB's location.

    Keeps the two stores co-located so a scratch/in-memory metadata DB gets a
    scratch/in-memory vector DB too (tests never touch the real repo files):
    - explicit `vector_db_path` wins;
    - `:memory:` metadata → `:memory:` vectors;
    - a real metadata file → a `vectors.sqlite` sibling next to it;
    - no metadata path → the packaged default.
    """
    if vector_db_path is not None:
        return vector_db_path
    if db_path is None:
        return DEFAULT_VECTOR_DB_PATH
    if str(db_path) == ":memory:":
        return ":memory:"
    return Path(db_path).with_name("vectors.sqlite")


def _today() -> str:
    return date.today().isoformat()


def _as_tag_list(value) -> list[str]:
    """Coerce a frontmatter `tags` value into a list of strings.

    YAML may parse tags as a single scalar, a list, or omit them entirely.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _as_date_str(value) -> str | None:
    """Normalize a frontmatter date (YAML may give a real `date`) to an ISO string."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _mtime_iso(path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
