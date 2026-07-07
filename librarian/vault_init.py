"""Initialize a fresh vault: folder structure + seeded schema.json.

The vault is a separate git repo from this code (it holds personal data). This
makes standing up a new one — on your laptop or the VPS — a single command.
Folders are derived from the schema so they never drift from it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from librarian.store.schema import TEMPLATE_SCHEMA_PATH, Schema

# Non-type directories every vault needs.
SPECIAL_DIRS = [".raw", ".trash", "system/MOC"]


def init_vault(root: str | Path, schema_path: str | Path = TEMPLATE_SCHEMA_PATH) -> Path:
    """Create the vault tree under `root` and seed `system/schema.json`.

    Idempotent: existing folders and an existing schema are left untouched.
    """
    root = Path(root)
    schema = Schema.load(schema_path)

    for spec in schema.types.values():
        (root / spec.folder).mkdir(parents=True, exist_ok=True)
    for d in SPECIAL_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    dest = root / "system" / "schema.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copyfile(str(schema_path), str(dest))

    return root
