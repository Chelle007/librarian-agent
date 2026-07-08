"""Direct file I/O for the Obsidian vault.

The vault is a plain folder of markdown files with YAML frontmatter. This module
is the only place that touches those files on disk. No running Obsidian instance
or REST API is involved — Obsidian is just an optional GUI viewer on the same
folder.

Responsibilities (Stage 1, no LLM):
- read/write markdown + frontmatter
- `.raw/` immutable, date-archived write-through of every original input
- `.trash/` soft-delete (move, never `rm`)

Folder *routing* (type -> folder) lives in the write pipeline via `schema.py`;
this module takes an explicit destination folder and just does the I/O.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from librarian.vault_folders import SYSTEM_FOLDER

_REPO_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = ".raw"
TRASH_DIR = ".trash"

# Top-level dirs that hold non-note content and must never be indexed as notes.
# (.raw/.trash are also skipped by the hidden-path guard, listed here for clarity.)
NON_NOTE_DIRS = {RAW_DIR, TRASH_DIR, "system", SYSTEM_FOLDER}


def default_vault_root() -> Path:
    """Where the vault lives when not given explicitly.

    The vault is a separate repo from this code, so by default it sits as a
    *sibling* of the code repo (e.g. `.../librarian agent/vault`). Override with
    the LIBRARIAN_VAULT env var or a `--vault` flag / `vault_root=` argument.
    """
    env = os.environ.get("LIBRARIAN_VAULT")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT.parent / "vault"


# Backwards-compatible alias; resolved at import time.
DEFAULT_VAULT_ROOT = default_vault_root()


class VaultIOError(Exception):
    """Raised on unsafe or impossible vault operations."""


@dataclass
class Note:
    """An in-memory note: frontmatter + markdown body + on-disk location."""

    frontmatter: dict = field(default_factory=dict)
    body: str = ""
    path: Path | None = None


class VaultIO:
    def __init__(self, vault_root: str | Path | None = None):
        root = vault_root if vault_root is not None else default_vault_root()
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------ read
    def read(self, path: str | Path) -> Note:
        p = self._resolve_inside(path)
        if not p.is_file():
            raise VaultIOError(f"note not found: {p}")
        post = frontmatter.load(str(p))
        return Note(frontmatter=dict(post.metadata), body=post.content, path=p)

    def iter_notes(self):
        """Yield every content note in the vault (for a full index rebuild).

        Walks all markdown under the vault root, skipping non-note areas
        (`.raw/`, `.trash/`, `system/`) and any hidden file/dir. This is the
        source-of-truth walk the metadata index is rebuilt from.
        """
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root)
            if rel.parts[0] in NON_NOTE_DIRS:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            post = frontmatter.load(str(p))
            yield Note(frontmatter=dict(post.metadata), body=post.content, path=p)

    # ----------------------------------------------------------------- write
    def write(
        self,
        *,
        frontmatter: dict,
        body: str = "",
        folder: str,
        slug: str | None = None,
    ) -> Path:
        """Write a note into `folder`, returning the path.

        Filename is derived from `slug` (or frontmatter name/title, else a
        timestamp), made unique so an existing note is never clobbered.
        """
        dest_dir = self._resolve_inside(folder)
        dest_dir.mkdir(parents=True, exist_ok=True)

        base = _slugify(
            slug
            or frontmatter.get("name")
            or frontmatter.get("title")
            or _timestamp_slug()
        )
        path = _unique_path(dest_dir, base)
        self._dump(path, frontmatter, body)
        return path

    def overwrite(self, path: str | Path, *, frontmatter: dict, body: str = "") -> Path:
        """Update an existing note in place (used by the update path)."""
        p = self._resolve_inside(path)
        if not p.is_file():
            raise VaultIOError(f"cannot overwrite, note not found: {p}")
        self._dump(p, frontmatter, body)
        return p

    # ------------------------------------------------------------------- raw
    def archive_raw(self, text: str, *, note_id: str | None = None, when: datetime | None = None) -> Path:
        """Write an immutable snapshot of the original input to `.raw/`.

        Archived under `.raw/YYYY-MM-DD/`. Never overwrites an existing entry —
        this is an append-only audit trail.
        """
        when = when or datetime.now(timezone.utc)
        day_dir = self.root / RAW_DIR / when.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        base = when.strftime("%H%M%S")
        path = _unique_path(day_dir, base)
        meta = {"captured_at": when.isoformat()}
        if note_id:
            meta["note_id"] = note_id
        self._dump(path, meta, text)
        return path

    # ----------------------------------------------------------------- trash
    def soft_delete(self, path: str | Path) -> Path:
        """Move a note to `.trash/`, preserving its relative path. Never `rm`."""
        p = self._resolve_inside(path)
        if not p.is_file():
            raise VaultIOError(f"cannot delete, note not found: {p}")

        rel = p.relative_to(self.root)
        dest = self.root / TRASH_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = _unique_path(dest.parent, dest.stem)
        p.rename(dest)
        return dest

    # --------------------------------------------------------------- helpers
    def resolve(self, path: str | Path) -> Path:
        """Public: resolve an absolute/vault-relative path, guarded to the vault."""
        return self._resolve_inside(path)

    def relpath(self, path: str | Path) -> str:
        """Vault-relative POSIX path — the stable id used by the metadata store."""
        return self._resolve_inside(path).relative_to(self.root).as_posix()

    def _dump(self, path: Path, meta: dict, body: str) -> None:
        post = frontmatter.Post(body, **meta)
        path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    def _resolve_inside(self, path: str | Path) -> Path:
        """Resolve `path` (absolute or vault-relative) and forbid escaping root."""
        p = Path(path)
        p = p if p.is_absolute() else self.root / p
        p = p.resolve()
        if p != self.root and self.root not in p.parents:
            raise VaultIOError(f"path escapes vault root: {p}")
        return p


def _slugify(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value or _timestamp_slug()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _unique_path(directory: Path, base: str, ext: str = ".md") -> Path:
    candidate = directory / f"{base}{ext}"
    i = 1
    while candidate.exists():
        candidate = directory / f"{base}-{i}{ext}"
        i += 1
    return candidate
