"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from librarian.pipeline import Librarian
from librarian.vault_init import init_vault


@pytest.fixture
def temp_vault(tmp_path):
    """A scratch vault initialized from the packaged schema template."""
    vault = tmp_path / "vault"
    init_vault(vault)
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
