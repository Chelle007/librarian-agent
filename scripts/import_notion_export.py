"""Bulk-import a Notion Markdown export into the vault.

Usage
-----
1. In Notion: open **🏫 University** → `···` → **Export** → Markdown & CSV → include subpages.
2. Unzip the download into:
     librarian-agent/scripts/notion_export/
   (e.g. `scripts/notion_export/University/...`)
3. Run::

     python scripts/import_notion_export.py \\
       --source scripts/notion_export/University \\
       --area university

Options ``--dry-run`` and ``--layout flat|nested`` are supported.
Skips `.env`, Credentials, and paths already in the vault (use ``--force`` to overwrite).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_export import (  # noqa: E402
    clean_notion_export_md,
    notion_export_name,
    parse_export_date,
    rel_md_paths,
    should_skip_file,
    vault_rel_path,
)
from librarian.pipeline import Librarian  # noqa: E402
from librarian.vault_folders import NOTES_FOLDER  # noqa: E402
from scripts.reorganize_vault_areas import _build_moc  # noqa: E402
from scripts.vault_paths import NOTE_AREAS, area_folder  # noqa: E402

DEFAULT_SOURCE = _REPO / "scripts" / "notion_export"
MOC_PATH = "⚙️ system/MOC/index.md"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import Notion Markdown export into the vault.")
    p.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Folder containing exported .md files (e.g. scripts/notion_export/University)",
    )
    p.add_argument(
        "--area",
        choices=NOTE_AREAS,
        default="university",
        help="Vault note area (default: university)",
    )
    p.add_argument(
        "--layout",
        choices=("nested", "flat"),
        default="nested",
        help="nested keeps course folders; flat slugifies full path into one filename",
    )
    p.add_argument(
        "--tags",
        default="notion-import,university",
        help="Comma-separated tags (default: notion-import,university)",
    )
    p.add_argument(
        "--drop-top",
        action="store_true",
        default=True,
        help="Drop the first folder segment (e.g. University/) from paths (default: on)",
    )
    p.add_argument(
        "--no-drop-top",
        action="store_false",
        dest="drop_top",
        help="Keep the top folder segment in vault paths",
    )
    p.add_argument("--dry-run", action="store_true", help="Print planned imports only")
    p.add_argument("--force", action="store_true", help="Overwrite notes that already exist")
    return p.parse_args()


def _area_prefix(area: str) -> str:
    return f"{NOTES_FOLDER}/{area_folder(area)}"


def main() -> int:
    args = _parse_args()
    source = (args.source or DEFAULT_SOURCE).resolve()
    if not source.is_dir():
        print(
            f"Export folder not found: {source}\n\n"
            "Export Notion → Markdown & CSV, unzip into scripts/notion_export/, then re-run.",
            file=sys.stderr,
        )
        return 1

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    area_prefix = _area_prefix(args.area)
    md_files = rel_md_paths(source)
    if not md_files:
        print(f"No .md files under {source} (after skip rules).", file=sys.stderr)
        return 1

    lib = Librarian(vector_enabled=False)
    root = lib.vault.root
    created = updated = skipped = 0

    for md_file in md_files:
        if should_skip_file(md_file):
            skipped += 1
            continue

        rel_vault = vault_rel_path(
            md_file,
            source_root=source,
            area_folder=area_prefix,
            layout=args.layout,
            drop_top_segment=args.drop_top,
        )
        dest = root / rel_vault
        if dest.is_file() and not args.force:
            print(f"skip  {rel_vault} (exists; use --force to overwrite)")
            skipped += 1
            continue

        raw = md_file.read_text(encoding="utf-8")
        title = notion_export_name(md_file.stem)
        body = clean_notion_export_md(raw, title=title)
        body += f"\n\n---\n_Imported from Notion export: {md_file.relative_to(source)}_\n"
        created_date = parse_export_date(raw) or date.today().isoformat()
        fm = {"type": "note", "created_date": created_date, "tags": tags}

        if args.dry_run:
            action = "update" if dest.is_file() else "create"
            print(f"{action:6}  {rel_vault}")
            continue

        if dest.is_file():
            lib.vault.overwrite(dest, frontmatter=fm, body=body)
            lib.meta.upsert(path=rel_vault, type="note", tags=tags, created_date=created_date)
            updated += 1
            print(f"update  {rel_vault}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = lib.vault.write(
                frontmatter=fm,
                body=body,
                folder=str(dest.parent.relative_to(root)) + "/",
                slug=dest.stem,
            )
            rel_vault = lib.vault.relpath(written)
            lib.meta.upsert(path=rel_vault, type="note", tags=tags, created_date=created_date)
            created += 1
            print(f"create  {rel_vault}")

    if not args.dry_run and (created or updated):
        moc = root / MOC_PATH
        moc.parent.mkdir(parents=True, exist_ok=True)
        moc.write_text(_build_moc(root), encoding="utf-8")
        n = lib.reindex()
        lib.git.commit_and_push(f"import notion export: {created} created, {updated} updated")
        print(f"write {MOC_PATH}")
        print(f"reindexed {n} note(s)")

    lib.close()
    print(f"\nDone: {created} created, {updated} updated, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
