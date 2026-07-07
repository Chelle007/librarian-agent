"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from librarian.pipeline import Librarian
from librarian.store.schema import DEFAULT_SCHEMA_PATH


@pytest.fixture
def temp_vault(tmp_path):
    """A scratch vault dir with the real schema copied into vault/system/."""
    vault = tmp_path / "vault"
    (vault / "system").mkdir(parents=True)
    (vault / "system" / "schema.json").write_text(
        DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return vault


@pytest.fixture
def lib(temp_vault):
    lib = Librarian(
        vault_root=temp_vault,
        db_path=":memory:",
        schema_path=temp_vault / "system" / "schema.json",
    )
    yield lib
    lib.close()
