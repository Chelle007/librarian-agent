"""Vector store — chunk embeddings in SQLite via sqlite-vec.

Chunk-native (see the embedding/chunking decision doc): every row is a chunk, a
whole note is just a note with one chunk. Two tables kept in lockstep:

- `chunks`    — regular table: chunk_id, note_path, chunk_index, text (rowid PK)
- `vec_chunks`— sqlite-vec `vec0` virtual table: the float[dim] embedding, keyed
                by the same rowid.

Search runs KNN over chunks, then collapses hits to note granularity (a note's
score = its best/closest chunk), which is what recall@k is measured against.

Like the metadata store, this is a derived, rebuildable cache — the vault
markdown remains source of truth.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec
from sqlite_vec import serialize_float32

from librarian.ingestion.chunker import Chunk
from librarian.llm.embeddings import EMBED_DIM

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VECTOR_DB_PATH = _REPO_ROOT / "vectors.sqlite"


@dataclass
class NoteHit:
    """A retrieved note (best chunk collapsed up to note granularity)."""

    note_path: str
    distance: float          # L2 distance of the closest chunk (lower = nearer)
    chunk_id: str            # which chunk matched best
    chunk_index: int
    text: str                # that chunk's text (for RAG grounding)

    @property
    def score(self) -> float:
        """Cosine-ish similarity in [0, 1] for unit vectors (L2² = 2 − 2·cos)."""
        return 1.0 - (self.distance * self.distance) / 2.0


class VectorStore:
    def __init__(self, db_path: str | Path | None = None, *, dim: int = EMBED_DIM):
        self.dim = dim
        self._conn = sqlite3.connect(str(db_path if db_path is not None else DEFAULT_VECTOR_DB_PATH))
        self._conn.row_factory = sqlite3.Row
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id    TEXT UNIQUE NOT NULL,
                note_path   TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text        TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_note ON chunks(note_path)")
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{self.dim}])"
        )
        self._conn.commit()

    # ------------------------------------------------------------------ write
    def index_note(
        self, note_path: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> int:
        """Replace all vectors for a note with a fresh set. Returns chunk count.

        Delete-then-insert keeps update idempotent: re-indexing a changed note
        never leaves stale chunks behind.
        """
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")

        with self._conn:
            self._delete_note_rows(note_path)
            for chunk, emb in zip(chunks, embeddings):
                if len(emb) != self.dim:
                    raise ValueError(f"embedding dim {len(emb)} != store dim {self.dim}")
                cur = self._conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, note_path, chunk_index, text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (f"{note_path}#{chunk.index}", note_path, chunk.index, chunk.text),
                )
                self._conn.execute(
                    "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                    (cur.lastrowid, serialize_float32(emb)),
                )
        return len(chunks)

    def delete_note(self, note_path: str) -> None:
        with self._conn:
            self._delete_note_rows(note_path)

    def clear(self) -> None:
        """Drop every chunk. Backs a full vector rebuild (reindex)."""
        with self._conn:
            self._conn.execute("DELETE FROM vec_chunks")
            self._conn.execute("DELETE FROM chunks")

    def _delete_note_rows(self, note_path: str) -> None:
        rows = self._conn.execute(
            "SELECT rowid FROM chunks WHERE note_path = ?", (note_path,)
        ).fetchall()
        for r in rows:
            self._conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (r["rowid"],))
        self._conn.execute("DELETE FROM chunks WHERE note_path = ?", (note_path,))

    # ----------------------------------------------------------------- search
    def search(
        self,
        query_embedding: list[float],
        k: int = 5,
        *,
        note_paths: Iterable[str] | None = None,
    ) -> list[NoteHit]:
        """KNN over chunks, collapsed to the top-k distinct notes (best chunk each).

        `note_paths` restricts the result to a candidate set — this is the seam
        the hybrid path uses: the metadata store pre-filters (by type/tag/date) to
        a set of allowed notes, and only those compete in the vector ranking. To
        stay exhaustively correct within that subset (a normal top-N KNN could miss
        an allowed note ranked past N globally), we scan every chunk when a filter
        is given — sound and plenty fast at personal-vault scale.
        """
        if len(query_embedding) != self.dim:
            raise ValueError(f"query dim {len(query_embedding)} != store dim {self.dim}")

        allowed: set[str] | None = None
        if note_paths is not None:
            allowed = set(note_paths)
            if not allowed:
                return []

        # The KNN `k = ?` constraint must sit on the vec0 scan itself (a LIMIT on a
        # joined query isn't recognized), so it runs in a subquery, then we join.
        # Unfiltered: oversample so collapsing duplicate notes still leaves k.
        # Filtered: scan all chunks so no allowed note is dropped before filtering.
        if allowed is None:
            chunk_limit = max(k * 5, k + 10)
        else:
            chunk_limit = self.count_chunks()
            if chunk_limit == 0:
                return []

        rows = self._conn.execute(
            """
            SELECT c.note_path, c.chunk_id, c.chunk_index, c.text, v.distance
            FROM (
                SELECT rowid, distance
                FROM vec_chunks
                WHERE embedding MATCH ? AND k = ?
            ) v
            JOIN chunks c ON c.rowid = v.rowid
            ORDER BY v.distance
            """,
            (serialize_float32(query_embedding), chunk_limit),
        ).fetchall()

        best_by_note: dict[str, NoteHit] = {}
        for r in rows:
            if allowed is not None and r["note_path"] not in allowed:
                continue
            existing = best_by_note.get(r["note_path"])
            if existing is None or r["distance"] < existing.distance:
                best_by_note[r["note_path"]] = NoteHit(
                    note_path=r["note_path"],
                    distance=r["distance"],
                    chunk_id=r["chunk_id"],
                    chunk_index=r["chunk_index"],
                    text=r["text"],
                )

        hits = sorted(best_by_note.values(), key=lambda h: h.distance)
        return hits[:k]

    # -------------------------------------------------------------- lifecycle
    def count_chunks(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def count_notes(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(DISTINCT note_path) FROM chunks"
        ).fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
