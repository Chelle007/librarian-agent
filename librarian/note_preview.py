"""Short human-readable previews of vault notes for confirmation prompts."""

from __future__ import annotations

from pathlib import Path

from librarian.store.vault_io import VaultIO, VaultIOError


def format_note_preview(vault: VaultIO, path: str, *, max_body_chars: int = 100) -> str:
    """One-line summary: name/type plus an optional body snippet."""
    try:
        note = vault.read(path)
    except VaultIOError:
        return path

    fm = note.frontmatter
    stem = Path(path).name
    label_parts: list[str] = []
    if fm.get("name"):
        label_parts.append(str(fm["name"]))
    else:
        label_parts.append(stem)
    note_type = fm.get("type")
    if note_type:
        label_parts.append(f"({note_type})")

    label = " ".join(label_parts)
    snippet = " ".join(note.body.split())
    if snippet:
        if len(snippet) > max_body_chars:
            snippet = snippet[: max_body_chars - 1].rstrip() + "…"
        return f'{label} — "{snippet}"'
    return label


def format_target_confirm(
    vault: VaultIO,
    path: str,
    *,
    action: str = "update",
) -> str:
    """Ask the user to confirm a best-guess target instead of listing every candidate."""
    preview = format_note_preview(vault, path)
    if action == "delete":
        return (
            f"I think you want to delete {preview}. "
            f"Confirm to proceed, or tell me if you meant a different note."
        )
    return (
        f"I think you mean {preview}. "
        f"Confirm to update this note, or tell me if you meant a different one."
    )
