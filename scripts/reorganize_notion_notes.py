"""Move flat notion-import notes into notes/notion/<section>/ and build the MOC."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.pipeline import Librarian  # noqa: E402

# vault-relative paths
MOVES: list[tuple[str, str]] = [
    ("notes/hangout-ideas.md", "notes/notion/others/hangout-ideas.md"),
    ("notes/jb-travelling-plan.md", "notes/notion/others/jb-travelling-plan.md"),
    ("notes/sg-traveling-plan.md", "notes/notion/others/sg-traveling-plan.md"),
    ("notes/interview-practice-questions.md", "notes/notion/others/interview-practice-questions.md"),
    ("notes/desmond-bday-chalet-plan.md", "notes/notion/others/desmond-bday-chalet-plan.md"),
    ("notes/interview-tips-from-jia.md", "notes/notion/work/interview-tips-from-jia.md"),
    ("notes/job-application.md", "notes/notion/work/job-application.md"),
    ("notes/scholarship-interview-questions.md", "notes/notion/university/scholarship-interview-questions.md"),
]

MOC_SECTIONS = [
    ("others", "Travel, friends, hangouts"),
    ("work", "Job hunt, interviews, applications"),
    ("university", "Scholarships, coursework-adjacent personal notes"),
    ("hackathons", "Hackathon notes (pending migration)"),
    ("projects", "MAIBEL and side projects (pending migration)"),
    ("home", "Home / life admin (pending migration)"),
]

MOC_PATH = "system/MOC/notion.md"


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def _build_moc(root: Path) -> str:
    lines = [
        "---",
        "type: note",
        f"created_date: '{__import__('datetime').date.today().isoformat()}'",
        "tags:",
        "- moc",
        "- notion-import",
        "---",
        "",
        "# Notion import",
        "",
        "Hub for notes migrated from Notion. Each section mirrors a top-level Notion area.",
        "",
    ]
    notion_root = root / "notes" / "notion"
    for section, blurb in MOC_SECTIONS:
        section_dir = notion_root / section
        lines.append(f"## {section.title()}")
        lines.append(f"_{blurb}_")
        lines.append("")
        if section_dir.is_dir():
            notes = sorted(section_dir.glob("*.md"))
            if notes:
                for p in notes:
                    rel = p.relative_to(root).as_posix()
                    slug = p.stem
                    lines.append(f"- [[{slug}|{_title_from_slug(slug)}]] — `{rel}`")
            else:
                lines.append("- _Nothing migrated yet._")
        else:
            lines.append("- _Nothing migrated yet._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    lib = Librarian(vector_enabled=False)
    root = lib.vault.root

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

    moc_abs = root / MOC_PATH
    moc_abs.parent.mkdir(parents=True, exist_ok=True)
    moc_abs.write_text(_build_moc(root), encoding="utf-8")
    print(f"write {MOC_PATH}")

    n = lib.reindex()
    print(f"\nreindexed {n} note(s)")
    lib.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
