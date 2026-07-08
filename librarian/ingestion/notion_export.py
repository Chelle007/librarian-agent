"""Clean markdown from a Notion workspace export (Settings → Export → Markdown)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

SKIP_BASENAMES = frozenset(
    {
        ".env",
        "credentials",
        "index",
    }
)

SKIP_PATH_PARTS = frozenset(
    {
        "private & shared",
        "private and shared",
    }
)


_NOTION_FILE_ID = re.compile(r" [a-f0-9]{32}$", re.I)
_PAREN_MD_PATH = re.compile(r"\(([^()\n]*\.md)\)")
_NAV_BLOCK = re.compile(
    r"## Navigation\s*\n+(?:---\s*\n+)*(?:\[\[[^\]]+\]\]\s*\n+(?:---\s*\n+)*)+",
    re.MULTILINE,
)
_IMPORT_FOOTER = re.compile(
    r"\n---\n+_(?:Imported from Notion export|Migrated from Notion):[^\n]*_\s*$",
    re.MULTILINE,
)
_LOCAL_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_CSV_LINK = re.compile(r"\[[^\]]*\]\([^)]*\.csv[^)]*\)\s*")
_ASSET_LINK = re.compile(
    r"\[[^\]]*\]\([^)]*\.(?:pdf|docx?|png|jpe?g|gif|webp|zip)[^)]*\)\s*",
    re.I,
)
_SECTIONS_LIST = re.compile(r"## Sections\n\n(?:- \[\[[^\]]+\]\]\s*\n?)+")
_REDUNDANT_WRAPPER_DIRS = frozenset({"member-details"})


def notion_export_name(name: str) -> str:
    """Strip Notion's trailing file ID from export filenames."""
    return _NOTION_FILE_ID.sub("", str(name).strip())


def slugify_segment(name: str) -> str:
    """Match vault_io._slugify without importing private helper."""
    value = notion_export_name(name).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value or "untitled"


def should_skip_file(path: Path) -> bool:
    stem = path.stem.strip().lower()
    if stem in SKIP_BASENAMES:
        return True
    if stem.startswith("."):
        return True
    parts = {p.lower() for p in path.parts}
    if parts & SKIP_PATH_PARTS:
        return True
    return False


def rel_md_paths(source: Path) -> list[Path]:
    """All markdown files under source, sorted for stable imports."""
    if not source.is_dir():
        return []
    files = [p for p in sorted(source.rglob("*.md")) if not should_skip_file(p)]
    return files


def vault_rel_path(
    md_file: Path,
    *,
    source_root: Path,
    area_folder: str,
    layout: str,
    drop_top_segment: bool,
) -> str:
    """Vault-relative path like `📝 notes/🏫 university/csit314/exam.md`."""
    rel = md_file.relative_to(source_root)
    parts = list(rel.parts[:-1]) + [rel.stem]
    if drop_top_segment and len(parts) > 1:
        parts = parts[1:]
    segments = [slugify_segment(p) for p in parts[:-1]] + [slugify_segment(parts[-1])]
    if layout == "flat":
        slug = "-".join(segments)
        return f"{area_folder}/{slug}.md"
    if not segments:
        slug = slugify_segment(md_file.stem)
        return f"{area_folder}/{slug}.md"
    return f"{area_folder}/{'/'.join(segments)}.md"


def path_to_slug(path_str: str) -> str:
    path = unquote(path_str.replace("\\", "/"))
    filename = Path(path).name
    if filename.lower().endswith(".md"):
        filename = filename[:-3]
    return slugify_segment(filename)


def _md_link_to_wikilink(match: re.Match) -> str:
    label, target = match.group(1), unquote(match.group(2))
    if target.startswith(("http://", "https://", "mailto:")):
        return match.group(0)
    if target.lower().endswith(".csv"):
        slug = slugify_segment(notion_export_name(Path(target).stem))
        return f"[[{slug}|{label}]]"
    if target.lower().endswith(".md"):
        slug = path_to_slug(target)
        return f"[[{slug}|{label}]]" if label.lower().replace(" ", "-") != slug else f"[[{slug}]]"
    return match.group(0)


def _paren_md_to_wikilink(match: re.Match) -> str:
    target = unquote(match.group(1))
    if target.startswith(("http://", "https://", "mailto:")):
        return match.group(0)
    slug = path_to_slug(target)
    label = notion_export_name(Path(target).stem)
    return f"[[{slug}|{label}]]"


def strip_navigation_block(body: str) -> str:
    return _NAV_BLOCK.sub("", body)


def child_sections_block(folder: Path) -> str:
    """Markdown list of wikilinks for direct children of a hub folder."""
    items: list[str] = []
    dir_names = {p.name for p in folder.iterdir() if p.is_dir()}
    for p in sorted(folder.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            items.append(f"- [[{p.name}]]")
        elif p.suffix == ".md":
            if p.stem in dir_names or p.stem.lower() == "untitled":
                continue
            label = notion_export_name(p.stem).replace("-", " ").title()
            items.append(f"- [[{p.stem}|{label}]]")
    if not items:
        return ""
    return "## Sections\n\n" + "\n".join(items) + "\n\n"


def is_index_hub(hub_file: Path, body: str) -> bool:
    """True when a note should get an auto-generated child index."""
    folder = hub_file.parent / hub_file.stem
    if not folder.is_dir():
        return False
    children = [p for p in folder.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].suffix == ".md":
        return False
    child_dirs = [p for p in children if p.is_dir()]
    child_mds = [p for p in children if p.suffix == ".md" and p.stem.lower() != "untitled"]
    if not child_dirs and len(child_mds) < 2:
        return False
    substantive = _SECTIONS_LIST.sub("", body).strip()
    if len(substantive) > 3000:
        return False
    return True


def clean_vault_import_body(body: str) -> str:
    """Post-import cleanup for notes brought in from a Notion export."""
    body = body.replace("\r\n", "\n")
    body = strip_navigation_block(body)
    body = _CSV_LINK.sub("", body)
    body = _ASSET_LINK.sub("", body)
    body = _LOCAL_IMAGE.sub("", body)
    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _md_link_to_wikilink, body)
    body = _PAREN_MD_PATH.sub(_paren_md_to_wikilink, body)
    body = re.sub(r"https://www\.notion\.so/[^\s)]+", "", body)
    body = re.sub(r"https://app\.notion\.com/[^\s)]+", "", body)
    body = _IMPORT_FOOTER.sub("", body)
    body = re.sub(r"(?m)^!# ", "# ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def should_flatten_dir(child: Path, parent: Path) -> bool:
    if child.name == parent.name:
        return True
    return child.name in _REDUNDANT_WRAPPER_DIRS


def clean_notion_export_md(text: str, *, title: str | None = None) -> str:
    """Normalize Notion-exported markdown for the vault."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"(?m)^Exported on .*\n?", "", text)
    text = re.sub(r"(?m)^Notion .*\n?", "", text)
    text = re.sub(r"(?m)^# .+\n+", "", text, count=1)  # duplicate title line
    body = clean_vault_import_body(text)
    if title and body and not body.startswith("#"):
        body = f"# {title}\n\n{body}"
    return body


def parse_export_date(text: str) -> str | None:
    """Best-effort created_date from export footer."""
    m = re.search(r"Exported on (\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None
