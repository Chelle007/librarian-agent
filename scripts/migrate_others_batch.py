"""One-off: migrate Notion 'Others' pages into the vault."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_convert import notion_to_markdown  # noqa: E402
from librarian.pipeline import Librarian  # noqa: E402
from scripts.notion_sources_others import BDAY_FETCH, INTERVIEW_FETCH, PAGES  # noqa: E402

_DADT_JSON = Path(
    "/Users/michellechan/.cursor/projects/Users-michellechan-Documents-Fun-Projects-Librarian-Agent/agent-tools/844ec288-9ed3-4fd8-ae8d-ccbf03459185.txt"
)


def main() -> int:
    lib = Librarian(vector_enabled=False)
    created: list[str] = []

    extra = [
        {
            "title": "Interview Practice Questions",
            "slug": "interview-practice-questions",
            "created_date": "2025-10-10",
            "tags": ["notion-import", "others", "friends"],
            "url": "https://app.notion.com/p/26919407beda809f9b61e4a50b7ec06c",
            "raw": INTERVIEW_FETCH,
        },
        {
            "title": "Desmond Bday Chalet Plan",
            "slug": "desmond-bday-chalet-plan",
            "created_date": "2026-07-02",
            "tags": ["notion-import", "others", "friends"],
            "url": "https://app.notion.com/p/38b19407beda8044b25eeb1def89b317",
            "raw": BDAY_FETCH,
        },
    ]

    dadt_pages = []
    if _DADT_JSON.is_file():
        dadt = json.loads(_DADT_JSON.read_text(encoding="utf-8"))
        dadt_pages.append(
            {
                "title": "DADT",
                "slug": "dadt",
                "created_date": "2026-01-02",
                "tags": ["notion-import", "others", "friends", "desmond"],
                "url": dadt["url"],
                "raw": dadt["text"],
            }
        )

    for page in PAGES + extra + dadt_pages:
        body = notion_to_markdown(page["raw"])
        body += f"\n\n---\n_Migrated from Notion: {page['url']}_\n"
        res = lib.create(
            type="note",
            fields={"created_date": page["created_date"], "tags": page["tags"]},
            body=body,
            slug=page["slug"],
            raw_text=page["raw"],
        )
        if not res.ok:
            print(f"FAIL {page['slug']}: {res.message}", file=sys.stderr)
            return 1
        created.append(res.path or page["slug"])
        print(f"OK  {res.path}")

    lib.close()
    print(f"\nMigrated {len(created)} note(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
