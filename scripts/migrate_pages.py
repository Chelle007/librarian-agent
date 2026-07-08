"""Migrate Notion pages from scripts/notion_pages/*.raw into the vault."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_convert import notion_to_markdown  # noqa: E402
from librarian.pipeline import Librarian  # noqa: E402
from scripts.reorganize_vault_areas import _build_moc  # noqa: E402
from librarian.vault_folders import MOC_PATH
from scripts.vault_paths import upsert_note  # noqa: E402

PAGES_DIR = Path(__file__).resolve().parent / "notion_pages"
MANIFEST = PAGES_DIR / "manifest.json"


def _link_person(body: str, name: str) -> str:
    """Link a person name unless already a wikilink."""
    return re.sub(rf"(?<!\[\[)\b{name}\b(?!\|)", f"[[{name}]]", body)


def _strip_maibel_nav(body: str) -> str:
    lines = []
    skip = False
    for line in body.splitlines():
        s = line.strip()
        if s in ("## Navigation", "Navigation") or s == "[[maibel]]":
            skip = True
            continue
        if skip and s.startswith("---"):
            continue
        if skip and s in (
            "Behavior Test",
            "Prompt Check",
            "Sandbox Experiments",
            "Conclusion",
            "Model Comparison",
            "- Behavior Test",
            "- Prompt Check",
            "- Sandbox Experiments",
            "- Conclusion",
            "- Model Comparison",
        ):
            continue
        if skip and not s:
            skip = False
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _strip_maibel_directory(body: str) -> str:
    """Hub already has ## Sections wikilinks — drop duplicate Directory block."""
    return re.sub(
        r"(?ms)^# Directory\s*\n---\s*\n.*?(?=^# Technical Resources)",
        "",
        body,
    ).strip()


def _dedup_simyen_hub(body: str) -> str:
    body = body.replace(
        "- Meeting Suggestion",
        "- [[meeting-suggestion|Meeting Suggestion]]",
    )
    body = body.replace(
        "- Budgemon App Overview",
        "- [[budgemon-app-overview|Budgemon App Overview]]",
    )
    # Michelle's full pet pitch lives in budgemon-app-overview — keep a pointer here.
    body = re.sub(
        r"(?ms)^Michelle\s*\n\nidea: money manager pet.*?(?=^Reynaldi\s*$)",
        "Michelle — money manager pet idea → [[budgemon-app-overview]].\n\n",
        body,
        count=1,
    )
    return body


def _dedup_uolcssc_hub(body: str) -> str:
    body = body.replace("- TODO NutritionLM", "- [[nutritionlm-todo|TODO NutritionLM]]")
    body = body.replace("- Pitch Script", "- [[nutritionlm-pitch-script|Pitch Script]]")
    body = body.replace(
        "- Presentation flow",
        "- [[nutritionlm-presentation-flow|Presentation flow]]",
    )
    body = body.replace("- Puspak", "- [[puspak|Puspak]]")
    body = re.sub(
        r"(?ms)^## Ideas\s*\n\*\*NutritionLM — A Personal Health & Nutrition Companion\*\*\n.*?(?=^## 🎮 Gamification)",
        (
            "## Ideas\n"
            "**NutritionLM** — personal health & nutrition companion. "
            "Pitch narrative → [[nutritionlm-pitch-script]]; build checklist → [[nutritionlm-todo]].\n\n"
        ),
        body,
        count=1,
    )
    return body


def _postprocess(body: str, slug: str, area: str) -> str:
    body = _link_person(body, "Desmond")
    body = _link_person(body, "Mabel")

    if area == "projects" or slug == "maibel":
        body = re.sub(r"\bMAIBEL\b", "[[maibel]]", body)
        body = _strip_maibel_nav(body)

    if slug == "maibel":
        body = (
            "[[maibel]] — AI Prompting & Behavioral Intelligence Trial Sprint for Evren "
            "(founded by [[Mabel]]).\n\n"
            "## Sections\n"
            "- [[behavior-test]]\n"
            "- [[prompt-check]]\n"
            "- [[sandbox-experiments]]\n"
            "- [[maibel-conclusion]]\n"
            "- [[model-comparison]]\n\n"
            + body
        )
        body = _strip_maibel_directory(body)
    elif area == "projects" and slug != "maibel":
        body = f"Part of the [[maibel]] project ([[Mabel]]).\n\n{body}"

    if slug.startswith("simyen"):
        body = (
            "Team **Cool Beans** (SIMYEN Innovate for Impact 2025). "
            "FinTech track — money manager pet idea.\n\n" + body
        )
        body = _dedup_simyen_hub(body)
    if slug.startswith("uolcssc"):
        body = (
            "Team **Cool Beans** — **NutritionLM** (UOLCSSC Hackathon 2025).\n\n" + body
        )
        body = _dedup_uolcssc_hub(body)
    if slug == "budgemon-app-overview":
        body = (
            "Child of [[simyen-hackathon-2025]] — Budgemon / money manager pet concept.\n\n"
            + body
        )
    if slug == "nutritionlm-todo":
        body = f"Child of [[uolcssc-hackathon-2025]].\n\n{body}"
    if slug == "nutritionlm-pitch-script":
        body = f"Child of [[uolcssc-hackathon-2025]].\n\n{body}"
    if slug == "meeting-suggestion":
        body = f"Child of [[simyen-hackathon-2025]].\n\n{body}"
    if slug == "simyen-todo-list":
        body = f"Child of [[simyen-hackathon-2025]].\n\n{body}"
    if slug == "budgemon-pitching-flow":
        body = f"Child of [[budgemon-app-overview]] ([[simyen-hackathon-2025]]).\n\n{body}"
    if slug == "reys-pitch":
        body = f"Child of [[budgemon-app-overview]].\n\n{body}"
    if slug == "nutritionlm-presentation-flow":
        body = f"Child of [[uolcssc-hackathon-2025]].\n\n{body}"
    if slug == "puspak":
        body = f"Child of [[uolcssc-hackathon-2025]].\n\n{body}"
    return body


def main() -> int:
    if not MANIFEST.is_file():
        print(f"Missing {MANIFEST}", file=sys.stderr)
        return 1

    pages = json.loads(MANIFEST.read_text(encoding="utf-8"))
    lib = Librarian(vector_enabled=False)
    ok = 0

    for page in pages:
        raw_path = PAGES_DIR / page["raw_file"]
        if not raw_path.is_file():
            print(f"warn  missing raw file {raw_path}", file=sys.stderr)
            continue
        raw = raw_path.read_text(encoding="utf-8")
        if not raw.strip().startswith("<"):
            raw = f"<content>\n{raw}\n</content>"
        body = _postprocess(notion_to_markdown(raw), page["slug"], page["area"])
        body += f"\n\n---\n_Migrated from Notion: {page['url']}_\n"

        res = upsert_note(
            lib,
            area=page["area"],
            slug=page["slug"],
            created_date=page["created_date"],
            tags=page["tags"],
            body=body,
        )
        if not res.ok:
            print(f"FAIL {page['slug']}: {res.message}", file=sys.stderr)
            return 1
        print(f"OK  {res.action} {res.path}")
        ok += 1

    moc = lib.vault.root / MOC_PATH
    moc.write_text(_build_moc(lib.vault.root), encoding="utf-8")
    print(f"write {MOC_PATH}")

    lib.reindex()
    lib.close()
    print(f"\nMigrated {ok} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
