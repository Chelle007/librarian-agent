"""Helpers for matching conversation context to vault paths."""

from __future__ import annotations

from pathlib import Path


def context_references_path(context: str | None, path: str) -> bool:
    """True when conversation context ties to a specific note path."""
    if not context:
        return False
    if path in context:
        return True
    name = Path(path).name
    return bool(name and name in context)
