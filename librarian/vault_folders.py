"""Canonical Obsidian vault folder names (emoji prefixes for browse)."""

from __future__ import annotations

NOTES_FOLDER = "📝 notes"
TASKS_FOLDER = "✅ tasks"
HABITS_FOLDER = "🔁 habits"
INBOX_FOLDER = "📥 inbox"
SYSTEM_FOLDER = "⚙️ system"
CONTACTS_FOLDER = "👤 contacts"

# Obsidian folder names for note domains under NOTES_FOLDER.
AREA_FOLDERS: dict[str, str] = {
    "travel": "✈️ travel",
    "social": "💬 social",
    "work": "💼 work",
    "university": "🏫 university",
    "hackathons": "🏆 hackathons",
    "projects": "📂 projects",
    "home": "🏠 home",
}

AREA_HEADERS: dict[str, str] = {
    "travel": "✈️ Travel",
    "social": "💬 Social",
    "work": "💼 Work",
    "university": "🏫 University",
    "hackathons": "🏆 Hackathons",
    "projects": "📂 Projects",
    "home": "🏠 Home",
}

MOC_PATH = f"{SYSTEM_FOLDER}/MOC/index.md"

# Plain → emoji renames (idempotent; skips if dest already exists).
TOP_LEVEL_RENAMES: list[tuple[str, str]] = [
    ("notes", NOTES_FOLDER),
    ("tasks", TASKS_FOLDER),
    ("habits", HABITS_FOLDER),
    ("inbox", INBOX_FOLDER),
    ("system", SYSTEM_FOLDER),
    ("contacts", CONTACTS_FOLDER),
]
