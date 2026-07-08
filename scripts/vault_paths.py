"""Vault path helpers — organize notes by domain, not import source.

Librarian retrieval uses tags + embedded body text, not folder names. Folders
still matter for humans in Obsidian and for coarse browse/filter; they should
match *how you'd ask* ("travel plans", "job apps"), not where data came from.
Provenance stays in tags (`notion-import`).
"""

from __future__ import annotations

from librarian.pipeline import Librarian, WriteResult
from librarian.vault_folders import (
    AREA_FOLDERS,
    AREA_HEADERS,
    CONTACTS_FOLDER,
    NOTES_FOLDER,
    TOP_LEVEL_RENAMES,
)

# Domains the vault is organized around (matches likely query filters).
NOTE_AREAS = (
    "travel",
    "social",
    "work",
    "university",
    "hackathons",
    "projects",
    "home",
)

# Slug → area for migrated Notion pages (split old "others" by topic).
SLUG_AREA: dict[str, str] = {
    "hangout-ideas": "social",
    "jb-travelling-plan": "travel",
    "sg-traveling-plan": "travel",
    "interview-practice-questions": "social",
    "desmond-bday-chalet-plan": "social",
    "interview-tips-from-jia": "work",
    "job-application": "work",
    "scholarship-interview-questions": "university",
}


def area_folder(area: str) -> str:
    if area not in AREA_FOLDERS:
        raise ValueError(f"unknown area: {area}")
    return AREA_FOLDERS[area]


def note_path(area: str, slug: str) -> str:
    """Vault-relative path for a note under NOTES_FOLDER/<area>/."""
    return f"{NOTES_FOLDER}/{area_folder(area)}/{slug}.md"


def upsert_note(
    lib: Librarian,
    *,
    area: str,
    slug: str,
    created_date: str,
    tags: list[str],
    body: str,
) -> WriteResult:
    """Create or update a note under NOTES_FOLDER/<area>/."""
    path = note_path(area, slug)
    if (lib.vault.root / path).is_file():
        return lib.update(path, body=body)

    fm = {"type": "note", "created_date": created_date, "tags": tags}
    abs_path = lib.vault.write(
        frontmatter=fm,
        body=body,
        folder=f"{NOTES_FOLDER}/{area_folder(area)}/",
        slug=slug,
    )
    rel = lib.vault.relpath(abs_path)
    lib.meta.upsert(
        path=rel,
        type="note",
        tags=tags,
        created_date=created_date,
    )
    lib.git.commit_and_push(f"create {rel}")
    return WriteResult(status="done", action="created", note_id=rel, path=rel, message=f"created {rel}")
