# Librarian Agent

Personal AI assistant with a two-agent design: a thin **PA Agent** (Telegram,
conversation, reminders — Stage 3) and a custom **Librarian Agent** (vault
retrieval, schema enforcement, and writes — Stages 1–2).

The Librarian reads/writes Obsidian markdown **directly on disk** — no running
Obsidian instance or REST API required. Desktop and VPS stay in sync via git.

## Status

| Stage | Scope | Status |
|---|---|---|
| **1 — Baseline** | Vault I/O, schema validation, SQLite index, CLI | Done |
| **2 — Eval** | Classification, retrieval, RAG, agent orchestration, eval/benchmark | Core done (live eval: semantic/hybrid recall@5=100% on 20 vault cases) |
| **3 — PA** | Hermes, Telegram, MCP server wiring | Not started |

190 pytest tests. Stage 2 is usable via `librarian handle` / `librarian confirm`
from the CLI; MCP server packaging is Stage 3.

## Docs

| Doc | Contents |
|---|---|
| [`docs/Project Description.md`](docs/Project%20Description.md) | Goals, use cases, stack |
| [`docs/Architecture (7 July).md`](docs/Architecture%20(7%20July).md) | Full system design and rationale |
| [`docs/Build_Plan (Jul 6).md`](docs/Build_Plan%20(Jul%206).md) | Staged checklist |
| [`docs/Stage 2 Architecture Flow (Jul 9).md`](docs/Stage%202%20Architecture%20Flow%20(Jul%209).md) | `LibrarianAgent.handle()` flow diagram |
| [`docs/Embedding & Chunking Decision (Jul 7).md`](docs/Embedding%20%26%20Chunking%20Decision%20(Jul%207).md) | Embedding model + chunking policy |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GEMINI_API_KEY for handle / eval / benchmark
pytest

# scaffold vault (default: sibling ../vault)
python -m librarian.cli init
export LIBRARIAN_VAULT=/path/to/vault   # optional; skip --vault afterwards
```

Vault location resolution: `--vault` flag → `LIBRARIAN_VAULT` env → sibling `../vault`.
The vault is a **separate git repo** from this code repo (personal data).

## CLI

### Stage 1 — deterministic pipeline (no LLM)

```bash
python -m librarian.cli create --type contact --field name=Alex --tag friend
python -m librarian.cli update contacts/alex.md --field likes=coffee
python -m librarian.cli query --type contact
python -m librarian.cli delete notes/an-idea.md
python -m librarian.cli reindex          # rebuild metadata + vector index from vault
```

### Stage 2 — full agent (requires `GEMINI_API_KEY`)

```bash
python -m librarian.cli handle "how many contacts do I have?"
python -m librarian.cli handle "save Alex as a friend who likes coffee"
python -m librarian.cli handle "desmond is my bf actually" --context "…prior turns…"

# after needs_clarification (pending_id printed on stderr):
python -m librarian.cli confirm <pending_id> --approve
python -m librarian.cli confirm <pending_id> --reject
```

### Eval and benchmark

```bash
python -m librarian.cli eval --path semantic -k 5
python -m librarian.cli eval --path hybrid --generator gemini -n 20
python -m librarian.cli benchmark --arms A,B
```

Global flags: `--vault`, `--db`, `--schema`, `--git` (commit each write).

## Agent contract

`LibrarianAgent.handle()` is the single free-text entrypoint (CLI today, MCP in Stage 3).

```python
handle(raw_request, context=None, *, pending_id=None, approved=None) -> HandleResult
```

| Field | Meaning |
|---|---|
| `status` | `done` · `needs_clarification` · `error` |
| `message` | Human-readable response |
| `note_id` | Vault path when applicable |
| `action` | `created` · `updated` · `deleted` · `queried` · `None` |
| `pending_id` | Set when user must approve (mention / conflict / delete gates) |

Confirm path: `handle_confirm(pending_id, approved=…)` or `handle(..., pending_id=…, approved=…)`.

Corrections: when the classifier sets `is_reaction` (user pushback) or the user
approves a conflict overwrite, a row is logged to the `corrections` table **after**
a successful vault write — not as a log-only shortcut.

## Layout

```
librarian/
├── agent.py              # Stage 2 orchestrator (classify → route → gates → Stage 1)
├── classifier.py         # Combined intent + mode classification (one LLM call)
├── cli.py                # CLI harness (Stage 1 + Stage 2 commands)
├── pipeline.py           # Stage 1 Librarian — deterministic create/update/delete/query
├── write_resolution.py   # Create vs update vs clarify (schema identity first)
├── target_resolution.py  # Resolve vague refs for update/delete
├── mention_search.py     # Word-boundary vault scan for identity labels
├── link_resolution.py    # Wikilink merge + mention confirm formatting
├── note_preview.py       # One-line previews for confirm prompts
├── pending_confirm.py    # Pending TTL + snapshot helpers
├── vault_init.py         # Vault scaffolding
├── vault_folders.py      # Folder routing constants
├── benchmark/            # Token A/B benchmark (full agent vs passthrough)
├── eval/                 # Auto-generated retrieval eval (recall@k / MRR)
├── ingestion/            # Chunking + embed-text composition
├── llm/                  # Gemini client, embeddings, RAG, conflict check
├── retrieval/            # exact_lookup, semantic, hybrid
├── store/                # vault I/O, schema, metadata + vector stores
└── templates/            # schema.json seed
tests/
docs/
```

`pa/` (Hermes Telegram glue) is planned for Stage 3 — not in the repo yet.

## Vault layout

```
vault/
├── .raw/       # immutable original input archive
├── .trash/     # soft-delete destination
├── notes/      # generic fallback (type: note)
├── contacts/   # type: contact
├── tasks/      # type: task
├── habits/     # type: habit
├── inbox/      # type: brief
└── system/
    ├── schema.json
    └── MOC/
```

`schema.json` ships as a template in `librarian/templates/` and is copied into
`<vault>/system/schema.json` on `init`; the runtime reads the vault's live copy.
