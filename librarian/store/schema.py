"""Schema loader + validator for the Obsidian vault.

The vault follows a schema-on-read approach: `schema.json` is the single source
of truth for known note types and their required/optional frontmatter fields.
Unknown types are never rejected — they resolve to the fallback type (`note`)
and land in the generic bucket, to be formalized later via the clustering pass.

No LLM involved. Pure structural validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

# Default location: schema.json lives inside the vault so it travels with it.
# schema.py is at librarian/store/schema.py, so the repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = _REPO_ROOT / "vault" / "system" / "schema.json"


class SchemaError(Exception):
    """Raised when schema.json itself is malformed or cannot be loaded."""


class TypeSpec(BaseModel):
    """One note type's contract: where it lives and which fields it needs."""

    folder: str
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class Schema(BaseModel):
    """Parsed, validated representation of schema.json."""

    base_required: list[str] = Field(default_factory=list)
    types: dict[str, TypeSpec] = Field(default_factory=dict)
    fallback_type: str

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Schema":
        """Load and validate schema.json from disk."""
        path = Path(path) if path is not None else DEFAULT_SCHEMA_PATH
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SchemaError(f"schema.json not found at {path}") from exc
        except json.JSONDecodeError as exc:
            raise SchemaError(f"schema.json is not valid JSON: {exc}") from exc

        schema = cls.model_validate(raw)

        if schema.fallback_type not in schema.types:
            raise SchemaError(
                f"fallback_type '{schema.fallback_type}' is not defined in types"
            )
        return schema

    def is_known_type(self, type_name: str | None) -> bool:
        return type_name in self.types

    def resolve_type(self, type_name: str | None) -> str:
        """Map a possibly-unknown type onto a real type (schema-on-read).

        Unknown or missing types collapse to the fallback type.
        """
        if type_name in self.types:
            return type_name
        return self.fallback_type

    def folder_for(self, type_name: str | None) -> str:
        """Return the destination folder for a type (after fallback resolution)."""
        return self.types[self.resolve_type(type_name)].folder

    def validate_note(self, frontmatter: dict) -> "ValidationResult":
        """Validate a note's frontmatter against the schema.

        Checks base-required fields and the resolved type's required fields.
        An unknown type is not an error — it resolves to the fallback and only
        the fallback's (looser) requirements apply.
        """
        given_type = frontmatter.get("type")
        resolved_type = self.resolve_type(given_type)
        unknown_type = given_type is not None and given_type not in self.types

        missing: list[str] = []
        for f in self.base_required:
            if _is_empty(frontmatter.get(f)):
                missing.append(f)

        for f in self.types[resolved_type].required:
            if _is_empty(frontmatter.get(f)):
                missing.append(f)

        # de-dupe while preserving order
        missing = list(dict.fromkeys(missing))

        return ValidationResult(
            resolved_type=resolved_type,
            folder=self.types[resolved_type].folder,
            missing_required=missing,
            unknown_type=unknown_type,
        )


@dataclass
class ValidationResult:
    resolved_type: str
    folder: str
    missing_required: list[str] = field(default_factory=list)
    unknown_type: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.missing_required


def _is_empty(value) -> bool:
    """Treat None, empty string, and empty collections as missing."""
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)) and len(value) == 0:
        return True
    return False
