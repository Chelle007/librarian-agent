"""SQLite metadata store — the structured index over the vault.

This is a *derived, rebuildable cache*: the markdown files in the vault remain
the source of truth. The index exists for fast structured filtering (by type,
tag, date) without parsing every file — it backs the non-LLM `exact_lookup`
path and `librarian_query_raw`.

Also owns the `corrections` table: every revert/edit-via-reply is logged here.
That signal is lost forever if not captured at the moment it happens, so it's
part of the Stage 1 baseline even though Stage 1 is otherwise LLM-free.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = _REPO_ROOT / "index.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    path          TEXT PRIMARY KEY,   -- vault-relative path, stable id
    type          TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_date  TEXT,
    last_modified TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_date);

CREATE TABLE IF NOT EXISTS corrections (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id                TEXT,
    original_classification TEXT,
    corrected_to           TEXT,
    timestamp              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_note ON corrections(note_id);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    intent       TEXT NOT NULL,
    message      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    note_type    TEXT,
    target_path  TEXT,
    fields       TEXT NOT NULL DEFAULT '{}',
    body         TEXT,
    link_paths   TEXT NOT NULL DEFAULT '[]',
    tags         TEXT NOT NULL DEFAULT '[]',
    links        TEXT NOT NULL DEFAULT '[]',
    raw_request  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_confirmations(status);
"""


class MetadataStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ----------------------------------------------------------- write index
    def upsert(
        self,
        *,
        path: str,
        type: str,
        tags: list[str] | None = None,
        created_date: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Insert or update a note's index row, keyed by path."""
        last_modified = last_modified or _now_iso()
        self._conn.execute(
            """
            INSERT INTO notes (path, type, tags, created_date, last_modified)
            VALUES (:path, :type, :tags, :created_date, :last_modified)
            ON CONFLICT(path) DO UPDATE SET
                type          = excluded.type,
                tags          = excluded.tags,
                created_date  = excluded.created_date,
                last_modified = excluded.last_modified
            """,
            {
                "path": path,
                "type": type,
                "tags": json.dumps(tags or []),
                "created_date": created_date,
                "last_modified": last_modified,
            },
        )
        self._conn.commit()

    def delete(self, path: str) -> None:
        """Drop a note from the index (file soft-delete is handled by VaultIO)."""
        self._conn.execute("DELETE FROM notes WHERE path = ?", (path,))
        self._conn.commit()

    def replace_notes(self, rows: list[dict]) -> None:
        """Atomically rebuild the entire `notes` table from `rows`.

        Backs `reindex`: the index is a derived cache, so it can be dropped and
        rebuilt wholesale from the vault markdown (source of truth). Wrapped in a
        single transaction so a crash never leaves a half-rebuilt index. Only the
        `notes` table is touched — `corrections` (real signal, not derived) is
        left intact.
        """
        params = [
            {
                "path": r["path"],
                "type": r["type"],
                "tags": json.dumps(r.get("tags") or []),
                "created_date": r.get("created_date"),
                "last_modified": r.get("last_modified") or _now_iso(),
            }
            for r in rows
        ]
        with self._conn:  # implicit transaction: commit on success, rollback on error
            self._conn.execute("DELETE FROM notes")
            self._conn.executemany(
                """
                INSERT INTO notes (path, type, tags, created_date, last_modified)
                VALUES (:path, :type, :tags, :created_date, :last_modified)
                """,
                params,
            )

    def rename(self, old_path: str, new_path: str) -> None:
        self._conn.execute(
            "UPDATE notes SET path = ? WHERE path = ?", (new_path, old_path)
        )
        self._conn.commit()

    # ------------------------------------------------------------------ read
    def get(self, path: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE path = ?", (path,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def query(
        self,
        *,
        type: str | None = None,
        tag: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        order_by: str = "created_date",
        descending: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Structured filter over the index. No LLM — backs librarian_query_raw."""
        clauses: list[str] = []
        params: list = []

        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(notes.tags) WHERE value = ?)")
            params.append(tag)
        if created_after is not None:
            clauses.append("created_date >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_date <= ?")
            params.append(created_before)

        if order_by not in {"created_date", "last_modified", "type", "path"}:
            raise ValueError(f"invalid order_by column: {order_by}")

        sql = "SELECT * FROM notes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_by} {'DESC' if descending else 'ASC'}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count(self, *, type: str | None = None, tag: str | None = None) -> int:
        return len(self.query(type=type, tag=tag, limit=None))

    # ----------------------------------------------------------- corrections
    def log_correction(
        self,
        *,
        note_id: str | None,
        original_classification: str | None,
        corrected_to: str | None,
        timestamp: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO corrections
                (note_id, original_classification, corrected_to, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (note_id, original_classification, corrected_to, timestamp or _now_iso()),
        )
        self._conn.commit()

    def get_corrections(self, note_id: str | None = None) -> list[dict]:
        if note_id is None:
            rows = self._conn.execute(
                "SELECT * FROM corrections ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM corrections WHERE note_id = ? ORDER BY id", (note_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------ pending confirms
    def create_pending(
        self,
        *,
        kind: str,
        intent: str,
        message: str,
        raw_request: str,
        note_type: str | None = None,
        target_path: str | None = None,
        fields: dict | None = None,
        body: str | None = None,
        link_paths: list[str] | None = None,
        tags: list[str] | None = None,
        links: list[str] | None = None,
        ttl_seconds: int = 86400,
    ) -> str:
        pending_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        self._conn.execute(
            """
            INSERT INTO pending_confirmations (
                id, kind, intent, message, created_at, expires_at, status,
                note_type, target_path, fields, body, link_paths, tags, links, raw_request
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_id,
                kind,
                intent,
                message,
                now.isoformat(),
                (now + timedelta(seconds=ttl_seconds)).isoformat(),
                note_type,
                target_path,
                json.dumps(fields or {}, ensure_ascii=False),
                body,
                json.dumps(link_paths or []),
                json.dumps(tags or []),
                json.dumps(links or []),
                raw_request,
            ),
        )
        self._conn.commit()
        return pending_id

    def get_pending(self, pending_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM pending_confirmations WHERE id = ?", (pending_id,)
        ).fetchone()
        if row is None:
            return None
        data = _pending_row_to_dict(row)
        if data["status"] != "pending":
            return None
        if datetime.now(timezone.utc) >= _parse_iso(data["expires_at"]):
            self.settle_pending(pending_id, "expired")
            return None
        return data

    def settle_pending(self, pending_id: str, status: str) -> bool:
        cur = self._conn.execute(
            """
            UPDATE pending_confirmations
            SET status = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, pending_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # --------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MetadataStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


def _pending_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["fields"] = json.loads(d.get("fields") or "{}")
    d["link_paths"] = json.loads(d.get("link_paths") or "[]")
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["links"] = json.loads(d.get("links") or "[]")
    return d


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
