"""Stage 1 end-to-end smoke test.

Covers the Build Plan checklist in one scenario: create each known type,
fallback-bucket create, structured query, and soft-delete — plus one run
through the CLI harness.
"""

from __future__ import annotations

from librarian.cli import main
from librarian.vault_folders import SYSTEM_FOLDER


def test_end_to_end_scenario(lib):
    # 1-4. create each known type
    note = lib.create(type="note", body="a passing thought", raw_text="a passing thought")
    contact = lib.create(type="contact", fields={"name": "Alex", "birthday": "2000-05-01"})
    task = lib.create(type="task", fields={"due_date": "2026-07-10"}, body="file taxes")
    habit = lib.create(type="habit", fields={"name": "Drink water", "frequency": "8h"})
    for r in (note, contact, task, habit):
        assert r.ok, r.message

    # 5. fallback-bucket create (unknown type -> notes/, original type tagged)
    fallback = lib.create(type="recipe", body="carbonara", raw_text="carbonara")
    assert fallback.ok
    assert fallback.path.startswith("📝 notes/")
    assert "recipe" in lib.vault.read(fallback.path).frontmatter["tags"]

    # invalid create is rejected and leaves no trace
    bad = lib.create(type="contact")  # missing required name
    assert not bad.ok

    # 6. structured queries
    assert len(lib.query_raw(type="note")) == 2  # thought + fallback recipe
    assert lib.meta.count() == 5

    # 7. soft-delete
    deleted = lib.delete(contact.path)
    assert deleted.ok
    assert deleted.path.startswith(".trash/")
    assert (lib.vault.root / deleted.path).exists()
    assert lib.meta.get(contact.path) is None
    assert len(lib.query_raw(type="contact")) == 0

    # every ingest left a .raw/ snapshot (4 valid + 1 fallback + 1 invalid attempt)
    raw_files = list((lib.vault.root / ".raw").rglob("*.md"))
    assert len(raw_files) == 6


def test_cli_create_and_query(temp_vault, capsys):
    schema = str(temp_vault / SYSTEM_FOLDER / "schema.json")
    db = str(temp_vault / "index.sqlite")
    common = ["--vault", str(temp_vault), "--db", db, "--schema", schema]

    rc = main(common + ["create", "--type", "contact", "--field", "name=Sam", "--tag", "friend"])
    assert rc == 0
    assert "created 👤 contacts/sam.md" in capsys.readouterr().out

    rc = main(common + ["query", "--type", "contact"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "contacts/sam.md" in out
    assert "friend" in out
