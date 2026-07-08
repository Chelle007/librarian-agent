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
- [x] `librarian/store/metadata_store.py` — SQLite index (type/tags/dates) + structured query + atomic rebuild + `corrections` table
- [x] `librarian/store/git_sync.py` — commit+push-on-write helper (no-op if vault isn't a git repo / has no remote)
- [x] `librarian/pipeline.py` — write pipeline: build → validate → route → archive raw → write → index → commit; create/update/delete/query_raw
- [x] `librarian/cli.py` — CLI harness (`python -m librarian.cli init|create|update|query|delete|reindex`)
- [x] `reindex` — rebuild the metadata index from vault markdown (reconciles external edits / git pulls)
- [x] `librarian/vault_init.py` — one-command vault scaffolding, seeded from the schema template
- [x] end-to-end smoke test (create each type, fallback bucket, query, soft-delete, CLI)

## Layout

```
librarian/           # the Librarian agent package
├── store/           # vault I/O, schema, metadata + vector stores
├── retrieval/       # exact / semantic / hybrid retrieval (Stage 2)
├── ingestion/       # chunking (Stage 2)
├── llm/             # Gemini client (Stage 2+)
├── templates/       # schema.json template (seeds new vaults)
└── vault_init.py    # vault scaffolding
pa/                  # PA-side glue (Stage 3, Hermes)
tests/               # pytest suite
```

The Librarian reads/writes the vault's markdown files **directly on disk** — no
running Obsidian instance or REST API required. Obsidian is just an optional GUI
viewer pointed at the same folder; desktop and VPS stay in sync via git.

## The vault is a separate repo (a sibling folder)

The vault holds **personal data** and is intentionally **not** part of this code
repo. It lives *outside* it — by default as a sibling folder:

```
librarian agent/
├── librarian-agent/      # this code repo
└── vault/                # your data — its own git repo
```

Resolution order for the vault location: `--vault` flag → `LIBRARIAN_VAULT` env
var → sibling `../vault`. Keep the vault as its own git repo so it can sync with
desktop Obsidian (obsidian-git) independently of the code.

`schema.json` ships as a template in `librarian/templates/` and is copied into
`<vault>/system/schema.json` on init; the runtime reads the vault's live copy so
it can evolve per-vault, falling back to the template if absent.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest

# create the vault (defaults to the sibling ../vault)
python -m librarian.cli init
# ...or put it anywhere
python -m librarian.cli --vault /path/to/vault init
export LIBRARIAN_VAULT=/path/to/vault   # so you can skip --vault afterwards
```

## CLI harness

```bash
python -m librarian.cli create --type note --body "an idea" --tag ml
python -m librarian.cli create --type contact --field name=Alex --field birthday=2000-01-01
python -m librarian.cli query --type contact
python -m librarian.cli update contacts/alex.md --field likes=coffee
python -m librarian.cli delete notes/an-idea.md
python -m librarian.cli reindex   # rebuild the index from vault markdown (after a git pull / external edits)
```

Add `--vault`, `--db`, `--schema` to point at a scratch vault, and `--git` to
commit each write.
