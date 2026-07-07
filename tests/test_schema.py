"""Tests for the schema loader + validator (store/schema.py)."""

from __future__ import annotations

import pytest

from store.schema import Schema, SchemaError, ValidationResult


@pytest.fixture(scope="module")
def schema() -> Schema:
    """Load the real vault schema.json."""
    return Schema.load()


def test_loads_real_schema(schema: Schema):
    assert schema.fallback_type == "note"
    assert "note" in schema.types
    assert "contact" in schema.types
    assert "habit" in schema.types


def test_valid_note(schema: Schema):
    result = schema.validate_note({"type": "note", "created_date": "2026-07-07"})
    assert result.is_valid
    assert result.resolved_type == "note"
    assert result.folder == "notes/"
    assert not result.unknown_type


def test_valid_contact(schema: Schema):
    result = schema.validate_note(
        {"type": "contact", "created_date": "2026-07-07", "name": "Alex"}
    )
    assert result.is_valid
    assert result.folder == "contacts/"


def test_contact_missing_required_name(schema: Schema):
    result = schema.validate_note({"type": "contact", "created_date": "2026-07-07"})
    assert not result.is_valid
    assert "name" in result.missing_required


def test_valid_habit(schema: Schema):
    result = schema.validate_note(
        {
            "type": "habit",
            "created_date": "2026-07-07",
            "name": "Drink water",
            "frequency": "8h",
        }
    )
    assert result.is_valid
    assert result.folder == "habits/"


def test_habit_missing_frequency(schema: Schema):
    result = schema.validate_note(
        {"type": "habit", "created_date": "2026-07-07", "name": "Drink water"}
    )
    assert not result.is_valid
    assert "frequency" in result.missing_required


def test_unknown_type_falls_back_to_note(schema: Schema):
    result = schema.validate_note({"type": "recipe", "created_date": "2026-07-07"})
    assert result.is_valid  # fallback 'note' has no extra required fields
    assert result.resolved_type == "note"
    assert result.folder == "notes/"
    assert result.unknown_type is True


def test_missing_base_required(schema: Schema):
    result = schema.validate_note({"type": "note"})  # no created_date
    assert not result.is_valid
    assert "created_date" in result.missing_required


def test_empty_string_counts_as_missing(schema: Schema):
    result = schema.validate_note(
        {"type": "contact", "created_date": "2026-07-07", "name": ""}
    )
    assert not result.is_valid
    assert "name" in result.missing_required


def test_folder_for_resolves_fallback(schema: Schema):
    assert schema.folder_for("contact") == "contacts/"
    assert schema.folder_for("does-not-exist") == "notes/"
    assert schema.folder_for(None) == "notes/"


def test_bad_schema_path_raises():
    with pytest.raises(SchemaError):
        Schema.load("/nonexistent/schema.json")


def test_validation_result_shape():
    r = ValidationResult(resolved_type="note", folder="notes/")
    assert r.is_valid
