"""Reorganize notes by domain area (drop notes/notion/) and refresh the MOC."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.pipeline import Librarian  # noqa: E402
from librarian.vault_folders import (  # noqa: E402
    AREA_FOLDERS,
    AREA_HEADERS,
    CONTACTS_FOLDER,
    HABITS_FOLDER,
    INBOX_FOLDER,
    MOC_PATH,
    NOTES_FOLDER,
    SYSTEM_FOLDER,
    TASKS_FOLDER,
    TOP_LEVEL_RENAMES,
)
from scripts.vault_paths import NOTE_AREAS, SLUG_AREA, note_path  # noqa: E402

# From notes/notion/<section>/ → notes/<area>/
MOVES: list[tuple[str, str]] = [
    (f"notes/notion/others/{slug}.md", note_path(area, slug))
    for slug, area in SLUG_AREA.items()
    if area in ("social", "travel")
] + [
    ("notes/notion/work/interview-tips-from-jia.md", note_path("work", "interview-tips-from-jia")),
    ("notes/notion/work/job-application.md", note_path("work", "job-application")),
    (
        "notes/notion/university/scholarship-interview-questions.md",
        note_path("university", "scholarship-interview-questions"),
    ),
]

AREA_BLURBS: dict[str, str] = {
    "travel": "Trips, itineraries, logistics",
    "social": "Friends, hangouts, events",
    "work": "Job hunt, interviews, applications",
    "university": "Scholarships, school-adjacent notes",
    "hackathons": "Hackathon notes",
    "projects": "MAIBEL and side projects",
    "home": "Life admin",
}

# Plain folder name → emoji folder (idempotent vault rename).
FOLDER_RENAMES: list[tuple[str, str]] = list(TOP_LEVEL_RENAMES)
for area, folder in AREA_FOLDERS.items():
    FOLDER_RENAMES.append((f"notes/{area}", f"notes/{folder}"))
    FOLDER_RENAMES.append((f"{NOTES_FOLDER}/{area}", f"{NOTES_FOLDER}/{folder}"))

OLD_MOC_PATHS = (
    "system/MOC/notion.md",
    f"{SYSTEM_FOLDER}/MOC/notion.md",
)


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def _notes_root(root: Path) -> Path:
    for candidate in (NOTES_FOLDER, "notes"):
        p = root / candidate
        if p.is_dir():
            return p
    return root / NOTES_FOLDER


def _build_moc(root: Path) -> str:
    lines = [
        "---",
        "type: note",
        f"created_date: '{date.today().isoformat()}'",
        "tags:",
        "- moc",
        "---",
        "",
        "# Vault index",
        "",
        "Browse by domain — how the Librarian filters mentally (`tag:work`, `tag:travel`, etc.).",
        "",
    ]
    contacts_dir = root / CONTACTS_FOLDER
    if not contacts_dir.is_dir():
        contacts_dir = root / "contacts"
    if contacts_dir.is_dir():
        contact_files = sorted(contacts_dir.glob("*.md"))
        if contact_files:
            lines.extend(
                [
                    "## 👤 Contacts",
                    "_People — birthdays, likes, relationships_",
                    "",
                ]
            )
            for p in contact_files:
                slug = p.stem
                lines.append(f"- [[{slug}|{_title_from_slug(slug)}]]")
            lines.append("")
    notes_root = _notes_root(root)
    for area in NOTE_AREAS:
        section_dir = notes_root / AREA_FOLDERS[area]
        if not section_dir.is_dir():
            section_dir = notes_root / area
        if not section_dir.is_dir():
            continue
        md_files = sorted(section_dir.rglob("*.md"))
        if not md_files:
            continue
        blurb = AREA_BLURBS.get(area, "")
        lines.append(f"## {AREA_HEADERS[area]}")
        if blurb:
            lines.append(f"_{blurb}_")
        lines.append("")
        for p in md_files:
            slug = p.stem
            lines.append(f"- [[{slug}|{_title_from_slug(slug)}]]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _prune_empty(root: Path, rel: str) -> None:
    p = root / rel
    if p.is_dir() and not any(p.rglob("*.md")):
        p.rmdir()
        parent = p.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()


def main() -> int:
    lib = Librarian(vector_enabled=False)
    root = lib.vault.root

    for src, dest in FOLDER_RENAMES:
        src_p, dest_p = root / src, root / dest
        if src_p.is_dir() and not dest_p.exists():
            src_p.rename(dest_p)
            print(f"rename  {src} -> {dest}")
        elif dest_p.is_dir():
            print(f"skip  already {dest}")

    for folder in (TASKS_FOLDER, HABITS_FOLDER, INBOX_FOLDER, f"{NOTES_FOLDER}/{AREA_FOLDERS['home']}"):
        (root / folder).mkdir(parents=True, exist_ok=True)

    for src, dest in MOVES:
        src_p = root / src
        dest_p = root / dest
        if not src_p.is_file():
            if dest_p.is_file():
                print(f"skip  already at {dest}")
                continue
            print(f"warn  missing {src}", file=sys.stderr)
            continue
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        src_p.rename(dest_p)
        print(f"move  {src} -> {dest}")

    for stale in ("notes/notion/others", "notes/notion/work", "notes/notion/university", "notes/notion"):
        _prune_empty(root, stale)

    moc_abs = root / MOC_PATH
    moc_abs.parent.mkdir(parents=True, exist_ok=True)
    moc_abs.write_text(_build_moc(root), encoding="utf-8")
    print(f"write {MOC_PATH}")

    for old_moc in OLD_MOC_PATHS:
        p = root / old_moc
        if p.is_file():
            p.unlink()
            print(f"remove {old_moc}")

    n = lib.reindex()
    print(f"\nreindexed {n} note(s)")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
