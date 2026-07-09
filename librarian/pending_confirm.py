"""Stored pending confirmations for multi-turn approve/reject (PA buttons, CLI confirm)."""

from __future__ import annotations

from librarian.classifier import Classification

DELETE_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 86400


def classification_from_pending(row: dict) -> Classification:
    return Classification(
        intent=row["intent"],
        note_type=row.get("note_type"),
        fields=row.get("fields") or {},
        tags=row.get("tags") or [],
        links=row.get("links") or [],
        body=row.get("body"),
    )
