"""Thin git wrapper for committing vault changes.

The vault is meant to be its own git repo (synced with desktop Obsidian via the
obsidian-git plugin). This helper commits after each write so the vault has a
full history and can be pushed/pulled. It degrades gracefully: if the vault
directory isn't a git repo (e.g. during tests), every call is a no-op.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitSync:
    def __init__(self, root: str | Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled and (self.root / ".git").is_dir()

    def commit(self, message: str) -> bool:
        """Stage everything and commit. Returns True if a commit was made."""
        if not self.enabled:
            return False
        subprocess.run(
            ["git", "-C", str(self.root), "add", "-A"],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "-C", str(self.root), "commit", "-m", message],
            capture_output=True,
            text=True,
        )
        # non-zero simply means "nothing to commit" — not an error we care about
        return result.returncode == 0

    def push(self) -> bool:
        if not self.enabled:
            return False
        result = subprocess.run(
            ["git", "-C", str(self.root), "push"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
