"""Reformat migrated Notion 'Others' notes in the vault."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_convert import (  # noqa: E402
    format_interview_practice,
    format_jb_travel_plan,
    format_sg_travel_plan,
    notion_to_markdown,
)
from librarian.pipeline import Librarian  # noqa: E402
from scripts.vault_paths import SLUG_AREA, note_path, upsert_note  # noqa: E402
from scripts.notion_sources_others import (  # noqa: E402
    BDAY_FETCH,
    INTERVIEW_FETCH,
    PAGES,
)

_DADT_JSON = Path(
    "/Users/michellechan/.cursor/projects/Users-michellechan-Documents-Fun-Projects-Librarian-Agent/agent-tools/844ec288-9ed3-4fd8-ae8d-ccbf03459185.txt"
)

FORMATTERS: dict[str, Callable[[str], str]] = {
    "hangout-ideas": notion_to_markdown,
    "jb-travelling-plan": format_jb_travel_plan,
    "sg-traveling-plan": format_sg_travel_plan,
    "interview-practice-questions": format_interview_practice,
    "desmond-bday-chalet-plan": notion_to_markdown,
}


def _body(formatter: Callable[[str], str], raw: str, url: str) -> str:
    return f"{formatter(raw)}\n\n---\n_Migrated from Notion: {url}_\n"


def main() -> int:
    lib = Librarian(vector_enabled=False)
    updated = 0

    entries = list(PAGES) + [
        {
            "slug": "interview-practice-questions",
            "created_date": "2025-10-10",
            "tags": ["notion-import", "others", "friends"],
            "url": "https://app.notion.com/p/26919407beda809f9b61e4a50b7ec06c",
            "raw": INTERVIEW_FETCH,
        },
        {
            "slug": "desmond-bday-chalet-plan",
            "created_date": "2026-07-02",
            "tags": ["notion-import", "others", "friends"],
            "url": "https://app.notion.com/p/38b19407beda8044b25eeb1def89b317",
            "raw": BDAY_FETCH,
        },
    ]

    if _DADT_JSON.is_file():
        pass  # dadt removed from vault — skip re-create

    for page in entries:
        slug = page["slug"]
        formatter = FORMATTERS[slug]
        area = SLUG_AREA[slug]
        path = note_path(area, slug)
        body = _body(formatter, page["raw"], page["url"])

        if (lib.vault.root / path).is_file():
            res = lib.update(path, body=body)
            action = "updated"
        else:
            res = upsert_note(
                lib,
                area=area,
                slug=slug,
                created_date=page["created_date"],
                tags=page["tags"],
                body=body,
            )
            action = res.action or "created"
            path = res.path or path

        if not res.ok:
            print(f"FAIL {slug}: {res.message}", file=sys.stderr)
            return 1
        print(f"OK  {action} {path}")
        updated += 1

    lib.close()
    print(f"\nReformatted {updated} note(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
