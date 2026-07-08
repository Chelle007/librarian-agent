"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from librarian.llm.embeddings import HashingEmbedder
from librarian.pipeline import Librarian
from librarian.vault_folders import SYSTEM_FOLDER
from librarian.vault_init import init_vault

# Small offline embedder for tests: deterministic, no network, no API key. Also
# insulates the suite from a real GEMINI_API_KEY that might be set in the dev env
# (which would otherwise make get_embedder() pick the live Gemini embedder).
TEST_EMBED_DIM = 256


@pytest.fixture(autouse=True)
def _force_offline_embedder(monkeypatch):
    """No test should hit the live Gemini API. Force get_embedder() offline."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("LIBRARIAN_EMBEDDER", "hashing")


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
        schema_path=temp_vault / SYSTEM_FOLDER / "schema.json",
        embedder=HashingEmbedder(dim=TEST_EMBED_DIM),
    )
    yield lib
    lib.close()
