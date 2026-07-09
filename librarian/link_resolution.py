"""Wikilink targets for create/update — merge without duplicates."""

from __future__ import annotations

from pathlib import Path

from librarian.mention_search import Mention
from librarian.note_preview import format_note_preview
from librarian.store.vault_io import VaultIO


def wikilink_label(path: str) -> str:
    return Path(path).stem


def links_from_paths(paths: list[str]) -> list[str]:
    return [wikilink_label(p) for p in paths]


def merge_links(existing: list | None, added: list[str]) -> list[str]:
    merged: list[str] = []
    for item in list(existing or []) + list(added):
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def apply_links(fields: dict, *, existing: dict | None, paths: list[str], extra: list[str]) -> dict:
    """Return ``fields`` with a merged ``links`` list from paths and extras."""
    out = dict(fields)
    labels = links_from_paths(paths) + [str(x).strip() for x in extra if str(x).strip()]
    merged = merge_links((existing or {}).get("links"), labels)
    if merged:
        out["links"] = merged
    return out


def format_mention_confirm(
    vault: VaultIO,
    label: str,
    mentions: list[Mention],
    *,
    action: str,
    original_request: str | None = None,
) -> str:
    lines = [f'I found "{label}" mentioned elsewhere in your vault:']
    for mention in mentions:
        lines.append(f"- {format_note_preview(vault, mention.path)}")
    if original_request and original_request.strip():
        lines.append(f'(You said: "{original_request.strip()}")')
    lines.append(f"Same entity? Confirm to {action} and link these notes.")
    return "\n".join(lines)


def mentions_confirmed(context: str | None, mentions: list[Mention], label: str) -> bool:
    if not context:
        return False
    if label.lower() in context.lower() and "mentioned elsewhere" in context.lower():
        return True
    return any(m.path in context or wikilink_label(m.path) in context for m in mentions)
