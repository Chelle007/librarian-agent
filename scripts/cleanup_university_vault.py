"""Clean up university notes after a Notion bulk import."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_export import (  # noqa: E402
    child_sections_block,
    clean_vault_import_body,
    is_index_hub,
    should_flatten_dir,
)
from librarian.pipeline import Librarian  # noqa: E402
from librarian.vault_folders import AREA_FOLDERS, MOC_PATH, NOTES_FOLDER  # noqa: E402
from scripts.reorganize_vault_areas import _build_moc  # noqa: E402

UNIVERSITY_DIR = f"{NOTES_FOLDER}/{AREA_FOLDERS['university']}"
DUPLICATE_ROOT_SLUGS = ("ai-901",)
_SECTIONS_BLOCK = re.compile(r"## Sections\n\n(?:- \[\[[^\]]+\]\]\s*\n?)+")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean university vault after Notion import.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def _university_root(lib: Librarian) -> Path:
    return lib.vault.root / UNIVERSITY_DIR


def _flatten_redundant_dirs(root: Path, *, dry_run: bool) -> int:
    flattened = 0
    candidates = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for child in candidates:
        parent = child.parent
        if not should_flatten_dir(child, parent):
            continue
        if dry_run:
            print(f"flatten  {child.relative_to(root.parent.parent)}")
            flattened += 1
            continue
        for item in sorted(child.iterdir()):
            dest = parent / item.name
            if dest.exists():
                if dest.is_dir() and item.is_dir():
                    for sub in sorted(item.iterdir()):
                        sub_dest = dest / sub.name
                        if sub_dest.exists():
                            if sub.is_file():
                                sub.unlink()
                            continue
                        shutil.move(str(sub), str(sub_dest))
                    item.rmdir()
                elif dest.is_file() and item.is_file():
                    item.unlink()
                else:
                    shutil.move(str(item), str(parent / f"{item.stem}-moved{item.suffix}"))
            else:
                shutil.move(str(item), str(dest))
        child.rmdir()
        print(f"flatten  {child.relative_to(root.parent.parent)}")
        flattened += 1
    return flattened


def _merge_ai901(root: Path, *, dry_run: bool) -> None:
    src = root / "ai-901.md"
    dest = root / "certifications" / "ai-901.md"
    if not src.is_file() or not dest.is_file():
        return
    src_body = src.read_text(encoding="utf-8")
    if "[Practice Assessment]" in src_body and "[Practice Assessment]" not in dest.read_text(encoding="utf-8"):
        dest_body = dest.read_text(encoding="utf-8")
        dest_body = dest_body.replace(
            "https://learn.microsoft.com/en-us/credentials/certifications/exams/ai-900/practice/assessment?assessment-type=practice&assessmentId=26",
            "[Practice Assessment](https://learn.microsoft.com/en-us/credentials/certifications/exams/ai-900/practice/assessment?assessment-type=practice&assessmentId=26)",
        )
        if not dry_run:
            dest.write_text(dest_body, encoding="utf-8")
    action = "remove " if dry_run else "removed"
    print(f"{action}  {UNIVERSITY_DIR}/ai-901.md (duplicate)")
    if not dry_run:
        src.unlink()


def _hub_body(hub_file: Path, body: str) -> str:
    body = clean_vault_import_body(body)
    if is_index_hub(hub_file, body):
        folder = hub_file.parent / hub_file.stem
        sections = child_sections_block(folder)
        if sections:
            if "## Sections" in body:
                body = _SECTIONS_BLOCK.sub(sections.rstrip() + "\n\n", body, count=1)
            else:
                body = sections + body
    else:
        body = _SECTIONS_BLOCK.sub("", body)
    return body.strip() + "\n"


def _clean_notes(lib: Librarian, root: Path, *, dry_run: bool) -> int:
    changed = 0
    for path in sorted(root.rglob("*.md")):
        rel = lib.vault.relpath(path)
        note = lib.vault.read(path)
        body = note.body
        if path.stem in DUPLICATE_ROOT_SLUGS and path.parent == root:
            continue
        cleaned_body = clean_vault_import_body(body)
        if is_index_hub(path, cleaned_body):
            new_body = _hub_body(path, body)
        else:
            new_body = _SECTIONS_BLOCK.sub("", cleaned_body).strip() + "\n"
        if path.stem == "untitled" and len(new_body.strip()) < 40:
            print(f"remove  {rel} (empty untitled)")
            if not dry_run:
                path.unlink()
            changed += 1
            continue
        if new_body.strip() == body.strip():
            continue
        print(f"clean   {rel}")
        if not dry_run:
            lib.vault.overwrite(path, frontmatter=dict(note.frontmatter), body=new_body)
        changed += 1
    return changed


def main() -> int:
    args = _parse_args()
    lib = Librarian(vector_enabled=False)
    root = _university_root(lib)
    if not root.is_dir():
        print(f"Missing {root}", file=sys.stderr)
        return 1

    flattened = _flatten_redundant_dirs(root, dry_run=args.dry_run)
    if not args.dry_run:
        _merge_ai901(root, dry_run=False)
    else:
        _merge_ai901(root, dry_run=True)

    cleaned = _clean_notes(lib, root, dry_run=args.dry_run)

    if not args.dry_run and (flattened or cleaned):
        moc = lib.vault.root / MOC_PATH
        moc.write_text(_build_moc(lib.vault.root), encoding="utf-8")
        n = lib.reindex()
        lib.git.commit_and_push(
            f"cleanup university vault: {flattened} flattened, {cleaned} cleaned"
        )
        print(f"write {MOC_PATH}")
        print(f"reindexed {n} note(s)")

    lib.close()
    print(f"\nDone: {flattened} flattened, {cleaned} cleaned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
