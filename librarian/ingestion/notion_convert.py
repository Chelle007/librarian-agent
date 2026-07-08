"""Convert Notion enhanced-markdown (from MCP fetch) to clean Obsidian markdown."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def notion_to_markdown(raw: str) -> str:
    text = _extract_content(raw)
    text = re.sub(r"<empty-block\s*/>", "", text)
    text = text.replace("<br>", "\n").replace("<br/>", "\n")
    text = _notion_toggles_to_details(text)
    text = _drop_sensitive_refs(text)

    text = re.sub(
        r"<details>\s*<summary>(.*?)</summary>\s*(.*?)\s*</details>",
        lambda m: (
            f"<details>\n<summary>{_inline_spans(m.group(1))}</summary>\n\n"
            f"{notion_to_markdown(m.group(2))}\n</details>"
        ),
        text,
        flags=re.DOTALL,
    )

    while True:
        m = re.search(r"<table>.*?</table>", text, re.DOTALL)
        if not m:
            break
        text = text[: m.start()] + _html_table_to_md(m.group(0)) + text[m.end() :]

    text = re.sub(r"<page[^>]*>\s*\.env\s*</page>", "", text, flags=re.I)
    text = re.sub(r"<page[^>]*>\s*Credentials\s*</page>", "", text, flags=re.I)
    text = re.sub(r"<page[^>]*>([^<]*)</page>", r"- \1", text)
    text = re.sub(r"<mention-page[^>]*>([^<]*)</mention-page>", r"\1", text)
    text = re.sub(r"<span([^>]*)>(.*?)</span>", _span_to_md, text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)

    text = text.replace("\\$", "$").replace("\\*", "*").replace("\\>", ">")
    text = _normalize_escapes(text)
    text = _fix_markdown_links(text)
    text = _fix_tables(text)
    text = re.sub(r"(?m)^- \.env\s*$", "", text)
    text = re.sub(r"(?m)^- Credentials\s*$", "", text, flags=re.I)
    text = _collapse_blank_lines(text)
    return text.strip()


def format_interview_tips_jia(raw: str) -> str:
    text = notion_to_markdown(raw)
    out: list[str] = []
    in_block = False  # Strength / Weakness nested bullets
    for line in text.splitlines():
        s = _strip_notion_chars(line.strip())
        if not s:
            continue
        if s.startswith("## "):
            in_block = False
            if out:
                out.append("")
            out.append(s)
            continue
        if s == "Basic question:":
            in_block = False
            out.append("")
            out.append("**Basic questions:**")
            continue
        if s in ("Cth:", "Cth"):
            out.append("  - _Examples:_")
            continue
        if s in ("Strength", "- Strength"):
            in_block = True
            out.append("- **Strength:**")
            continue
        if s in ("Weakness", "- Weakness"):
            in_block = True
            out.append("- **Weakness:**")
            continue
        if s.startswith("- Frequent question"):
            in_block = False
            out.append(s)
            continue
        if s.startswith("- ") and not in_block:
            out.append(s)
            continue
        if in_block:
            s = re.sub(r"^[•·]\s*", "", s)
            s = s.replace("->", "→")
            out.append(f"  - {s}")
            continue
        s = re.sub(r"^[•·]\s*", "- ", s)
        if s.startswith("- "):
            out.append(f"  {s}")
        elif s:
            out.append(s)
    return _collapse_blank_lines("\n".join(out))


def _strip_notion_chars(s: str) -> str:
    return re.sub(r"[\u200b\u2060⁠]+", "", s).strip()


def _clean_bullet_line(s: str) -> str:
    s = _strip_notion_chars(s)
    s = re.sub(r"^(\d+)\.\s*", r"\1. ", s)
    if s.startswith("- "):
        s = "- " + s[2:].lstrip()
    return s


def format_scholarship_questions(raw: str) -> str:
    text = _normalize_escapes(notion_to_markdown(raw))
    out: list[str] = []
    skip_intro = True
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if skip_intro and (
            s.startswith("Bold =")
            or s.startswith("The rest not sure")
        ):
            continue
        skip_intro = False
        if s.startswith("- ") and not line.startswith("  "):
            out.append(s)
        elif line.startswith("  "):
            out.append(line)
        else:
            out.append(s)
    intro = (
        "> **Bold** = you clearly remember they asked about this. "
        "The rest is approximate."
    )
    return _collapse_blank_lines(intro + "\n\n" + "\n".join(out))


def rows_to_markdown_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """Render DB rows as a markdown table. columns = [(header, key), ...]"""
    if not rows:
        return "_No entries yet._"
    header = "| " + " | ".join(h for h, _ in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = ["", header, sep]
    for row in rows:
        cells = []
        for _, key in columns:
            val = row.get(key) or ""
            val = str(val).replace("\n", "<br>")
            cells.append(val)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def format_sg_travel_plan(raw: str) -> str:
    lines = notion_to_markdown(raw).splitlines()
    out: list[str] = []
    for line in lines:
        if re.match(r"^\d{1,2} \w{3} \d{4}$", line.strip()):
            if out:
                out.append("")
            out.append(f"## {line.strip()}")
            continue
        out.append(line)
    return _collapse_blank_lines("\n".join(out))


def format_jb_travel_plan(raw: str) -> str:
    text = notion_to_markdown(raw)
    sections: list[tuple[str, list[str]]] = []
    current_title = "Stay"
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines, current_title
        if current_lines:
            sections.append((current_title, current_lines))
        current_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Place to stay:"):
            flush()
            current_title = "Stay"
            current_lines.append(stripped)
            continue
        if stripped == "Places to play:":
            flush()
            current_title = ""
            continue
        if stripped.startswith("- ") and not stripped.startswith("- Name:"):
            flush()
            current_title = stripped[2:].strip()
            continue
        current_lines.append(stripped)
    flush()

    parts: list[str] = []
    for title, lines in sections:
        if title == "Stay":
            parts.extend(["## Stay", ""] + lines + [""])
            continue
        if title:
            parts.extend([f"## {title}", ""])
        for ln in lines:
            if ln.startswith(("Name:", "Location:", "Website:", "Price:", "Themes:", "Additional:", "Alternative", "List:")):
                key, _, val = ln.partition(":")
                parts.append(f"- **{key.strip()}:** {val.strip()}")
            elif ln and not ln.startswith("- **"):
                parts.append(f"  {ln}")
            else:
                parts.append(ln)
        parts.append("")

    return _collapse_blank_lines("\n".join(parts).strip())


def format_interview_practice(raw: str) -> str:
    text = _normalize_escapes(notion_to_markdown(raw))
    parts: list[str] = []
    section: str | None = None
    buf: list[str] = []

    def flush_section() -> None:
        nonlocal buf, section
        if section in {"General Questions", "Random Questions"}:
            parts.append(_format_question_section(section, buf))
        else:
            if section == "Practice Guides":
                guides = _format_practice_guides(buf)
                parts.append(guides)
            elif section == "Comments":
                parts.append(_format_comments(buf))
            else:
                parts.extend(buf)
        buf = []

    for line in text.splitlines():
        if line.startswith("# "):
            flush_section()
            section = line[2:].strip()
            parts.append(line)
            parts.append("")
            continue
        if line.startswith("## "):
            if section == "Comments":
                buf.append(line)
            else:
                flush_section()
                parts.append(line)
                parts.append("")
            continue
        if line.startswith("### "):
            buf.append(line)
            continue
        buf.append(line)
    flush_section()

    return _collapse_blank_lines("\n".join(parts))


def _format_practice_guides(lines: list[str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("1. Legend:"):
            out.append(line)
            out.append("")
            out.append("> 🩷 Michelle's question · 💙 Jane's question · **[Random]** random · ==highlight== practiced")
            out.append("")
            i += 1
            if i < len(lines) and lines[i].strip().startswith("|"):
                out.append("")
            while i < len(lines) and lines[i].strip().startswith("|"):
                out.append(lines[i].strip())
                i += 1
            out.append("")
            continue
        if re.match(r"^3\.", line):
            out.append(line)
            i += 1
            while i < len(lines) and re.match(r"^[12]\.", lines[i].strip()):
                out.append(f"   {lines[i].strip()}")
                i += 1
            continue
        if re.match(r"^\d+\.", line) and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            out.append(line)
            out.append("")
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                out.append(lines[i].strip())
                i += 1
            out.append("")
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _format_comments(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append("")
            continue
        if s.startswith("#"):
            out.append(line)
            continue
        if s.startswith(("-", "+")):
            prefix = "❌" if s.startswith("-") else "✅"
            out.append(f"- {prefix} {s[1:].strip()}")
        else:
            out.append(line)
    return "\n".join(out)


def _format_question_section(title: str, lines: list[str]) -> str:
    out: list[str] = []
    block: list[str] = []
    q_title: str | None = None

    def flush_q() -> None:
        nonlocal block, q_title
        if not q_title:
            block = []
            return
        out.append(f"### {q_title}")
        out.append("")
        michelle: list[str] = []
        jane: list[str] = []
        mode: str | None = None
        for ln in block:
            s = ln.strip()
            if not s:
                continue
            if s.startswith("**Michelle"):
                mode = "m"
                continue
            if s.startswith("**Jane"):
                mode = "j"
                continue
            if mode == "m":
                michelle.append(f"*{s}*" if s.startswith("(") else s)
            elif mode == "j":
                jane.append(s)
            else:
                michelle.append(s)
        if michelle:
            out.extend(["#### Michelle", ""] + michelle + [""])
        if jane:
            out.extend(["#### Jane", ""] + jane + [""])
        block = []
        q_title = None

    for line in lines:
        m = re.match(r"^(\d+)\.\s+(.+)$", line.strip())
        if m:
            flush_q()
            q_title = f"{m.group(1)}. {m.group(2)}"
            continue
        if q_title is not None:
            block.append(line)
    flush_q()
    return "\n".join(out)


def _normalize_escapes(text: str) -> str:
    text = text.replace("\\t", "\t")
    return _tabs_to_spaces(text)


def _extract_content(fetch_text: str) -> str:
    m = re.search(r"<content>\n(.*)\n</content>", fetch_text, re.DOTALL)
    if m:
        return m.group(1)
    # Already bare content or full fetch payload
    m2 = re.search(r"<content>\n(.*)", fetch_text, re.DOTALL)
    return m2.group(1) if m2 else fetch_text


def _notion_toggles_to_details(text: str) -> str:
    """Convert `# Title {toggle=\"true\"}` + tab-indented body to <details>."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    toggle_re = re.compile(r"^(#{1,6})\s+(.+?)\s*\{toggle[^}]*\}\s*$")
    while i < len(lines):
        line = lines[i]
        m = toggle_re.match(line.strip())
        if not m:
            out.append(line)
            i += 1
            continue
        title = re.sub(r"\{color=[^}]+\}", "", m.group(2)).strip()
        i += 1
        block: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith("\t"):
                block.append(nxt.lstrip("\t"))
                i += 1
            elif not nxt.strip() and i + 1 < len(lines) and lines[i + 1].startswith("\t"):
                i += 1
            else:
                break
        body = "\n".join(block).strip()
        out.append(f"<details>\n<summary>{title}</summary>\n\n{body}\n</details>")
    return "\n".join(out)


def _drop_sensitive_refs(text: str) -> str:
    text = re.sub(r"\[.*?\]\([^)]*\.env[^)]*\)", "", text, flags=re.I)
    return text

def _span_to_md(match: re.Match[str]) -> str:
    attrs, content = match.group(1), match.group(2)
    inner = _inline_spans(content) if "<span" in content else content
    if "pink" in attrs:
        if len(inner) < 100 and not inner.startswith("**"):
            return f"{inner} 🩷"
        return f"**[Michelle]** {inner}"
    if "blue" in attrs:
        if len(inner) < 100 and not inner.startswith("**"):
            return f"{inner} 💙"
        return f"**[Jane]** {inner}"
    if "red" in attrs:
        return f"**[Random]** {inner}"
    if "underline" in attrs:
        return f"=={inner}=="
    if "green" in attrs:
        return inner
    return inner


def _inline_spans(text: str) -> str:
    return re.sub(r"<span([^>]*)>(.*?)</span>", _span_to_md, text, flags=re.DOTALL)


def _html_table_to_md(html: str) -> str:
    rows: list[list[str]] = []
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL):
        cells = re.findall(r"<t[hd]>(.*?)</t[hd]>", row, re.DOTALL)
        if cells:
            rows.append([_strip_md(_inline_spans(c)) for c in cells])
    if not rows:
        return ""
    lines: list[str] = [""]
    for i, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    lines.append("")
    return "\n".join(lines)


def _strip_md(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return s.replace("\\*", "*").replace("\\>", ">").strip()


def _tabs_to_spaces(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("\t"):
            depth = len(line) - len(line.lstrip("\t"))
            rest = line.lstrip("\t")
            if rest.startswith("|"):
                lines.append(rest)
            elif re.match(r"^\d+\.", rest):
                lines.append("   " * depth + rest)
            elif rest.startswith("- "):
                lines.append("  " * depth + rest)
            else:
                lines.append(rest)
        else:
            lines.append(line)
    return "\n".join(lines)


def _fix_markdown_links(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        if label == url or label.startswith("http"):
            return f"[{_link_label(url)}]({url})"
        return m.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, text)


def _link_label(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "maps.app.goo.gl" in host or "google.com" in host:
        return "Google Maps"
    if "instagram.com" in host:
        return "Instagram"
    if "agoda.com" in host:
        return "Agoda"
    if host:
        return host.removeprefix("www.")
    return "link"


def _fix_tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|"):
            if out and out[-1].strip():
                out.append("")
            while i < len(lines) and lines[i].strip().startswith("|"):
                out.append(lines[i].strip())
                i += 1
            out.append("")
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _collapse_blank_lines(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
