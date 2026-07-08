"""Tests for vault initialization (librarian/vault_init.py)."""

from __future__ import annotations

from librarian.store.schema import Schema
from librarian.vault_folders import SYSTEM_FOLDER
from librarian.vault_init import init_vault


def test_init_creates_structure_and_schema(tmp_path):
    root = tmp_path / "vault"
    init_vault(root)

    # schema seeded and loadable
    schema_file = root / SYSTEM_FOLDER / "schema.json"
    assert schema_file.is_file()
    schema = Schema.load(schema_file)

    # a folder exists for every type in the schema
    for spec in schema.types.values():
        assert (root / spec.folder).is_dir()

    # special dirs
    for d in (".raw", ".trash", f"{SYSTEM_FOLDER}/MOC"):
        assert (root / d).is_dir()


def test_init_is_idempotent_and_preserves_existing_schema(tmp_path):
    root = tmp_path / "vault"
    init_vault(root)
    (root / SYSTEM_FOLDER / "schema.json").write_text('{"custom": true}', encoding="utf-8")

    init_vault(root)  # second run must not clobber
    assert '"custom"' in (root / SYSTEM_FOLDER / "schema.json").read_text(encoding="utf-8")
