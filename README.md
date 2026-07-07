# Librarian Agent

Personal AI assistant with a two-agent design: a thin **PA Agent** (Telegram,
conversation, reminders) and a custom **Librarian Agent** (an MCP server that
owns the entire Obsidian vault — retrieval, schema enforcement, and writes).

See the design docs in `docs/`:

- `docs/Project Description.md` — goals, use cases, decisions
- `docs/Architecture (7 July).md` — full system architecture and rationale
- `docs/Build_Plan (Jul 6).md` — staged build plan

## Status

**Stage 1 — Librarian Baseline** (in progress). Vault I/O, schema validation,
metadata store, structured queries, CLI harness. **No LLM involved.**

Done:

- [x] `librarian/store/schema.py` — `schema.json` loader + validator (schema-on-read, fallback bucket)
- [x] `librarian/store/vault_io.py` — direct markdown+frontmatter I/O, `.raw/` write-through, `.trash/` soft-delete
- [x] `librarian/store/metadata_store.py` — SQLite index (type/tags/dates) + structured query + `corrections` table
- [x] `librarian/store/git_sync.py` — commit-on-write helper (no-op if vault isn't a git repo)
- [x] `librarian/pipeline.py` — write pipeline: build → validate → route → archive raw → write → index → commit; create/update/delete/query_raw
- [x] `librarian/cli.py` — CLI harness (`python -m librarian.cli create|update|query|delete`)
- [x] end-to-end smoke test (create each type, fallback bucket, query, soft-delete, CLI)

## Layout

```
librarian/           # the Librarian agent package
├── store/           # vault I/O, schema, metadata + vector stores
├── retrieval/       # exact / semantic / hybrid retrieval (Stage 2)
├── ingestion/       # chunking (Stage 2)
└── llm/             # Gemini client (Stage 2+)
pa/                  # PA-side glue (Stage 3, Hermes)
tests/               # pytest suite
vault/               # the Obsidian vault (schema.json lives in vault/system/)
```

The Librarian reads/writes the `vault/` markdown files **directly on disk** — no
running Obsidian instance or REST API required. Obsidian is just an optional GUI
viewer pointed at the same folder; desktop and VPS stay in sync via git.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## CLI harness

```bash
python -m librarian.cli create --type note --body "an idea" --tag ml
python -m librarian.cli create --type contact --field name=Alex --field birthday=2000-01-01
python -m librarian.cli query --type contact
python -m librarian.cli update contacts/alex.md --field likes=coffee
python -m librarian.cli delete notes/an-idea.md
```

Add `--vault`, `--db`, `--schema` to point at a scratch vault, and `--git` to
commit each write.
