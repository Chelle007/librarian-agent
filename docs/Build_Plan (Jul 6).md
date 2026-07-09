# Build Plan

> Last updated: July 9, 2026
> Status: Active
> See Architecture.md for full rationale behind each item below.

---

## Guiding principles (read first)

- **North star:** code sustainability > accuracy = token efficiency = latency. Clean, testable, deterministic-where-possible code beats micro-optimizations. Token efficiency is a design discipline + portfolio narrative (the A/B benchmark), never traded against correctness.
- **Librarian-first is deliberate.** The Librarian is the portfolio/resume centerpiece, so it's built and proven in isolation *before* the PA. The PA (Telegram, reminders) is intentionally last even though reminders are the #1 daily-use case — the resume story lives in the Librarian's retrieval/eval/benchmark engineering, not the bot glue.
- **Scope discipline — two tiers.** Every item is either **[CORE]** (must ship for the portfolio piece to be complete and demoable) or **[v2 — needs real data]** (designed and documented now, but *not* built for submission because it can't be validated without months of real usage). Don't let v2 items block core. The design write-up covers them either way — designing + honestly deferring is itself good resume material.

---

## Stage 1 — Librarian Baseline  ✅ (essentially done)

Vault I/O, schema validation, metadata store, structured queries, CLI test harness. No LLM involved. Goal: working, demoable vertical slice, submittable as a portfolio checkpoint.

- [x] Vault I/O module (`store/vault_io.py`) — read/write markdown + frontmatter
- [x] `.raw/` immutable write-through on every ingest (date-archived)
- [x] `schema.json` loader + validator (`store/schema.py`)
- [x] Metadata store (`store/metadata_store.py`) — SQLite table: type, path, tags, created_date, last_modified
- [x] Write pipeline: frontmatter build → schema validate → file write → metadata upsert → git commit **+ push**
- [x] Folder routing by `type` (deterministic, no LLM)
- [x] Generic fallback bucket (`notes/`, `type: note`) for unmatched content
- [x] `.trash/` soft-delete mechanism (move, not `rm`)
- [x] CLI test harness — manual create/update/query/delete calls against the pipeline directly
- [x] `librarian_query_raw(filter)` as a real tool (not just dev harness) — structured filter, no LLM
- [x] end-to-end smoke test (create each type, fallback bucket, query, soft-delete, CLI)
- [x] `corrections` table in metadata store — log every revert-via-reply event; signal is lost if not captured at the moment it happens, so it can't wait for Stage 2

**Remaining Stage 1 polish:**
- [x] Index reconcile/rebuild command (`reindex`) — rebuilds the SQLite `notes` table from the vault markdown (source of truth), atomically; reconciles external edits (desktop Obsidian / git pull). Skips `.raw/`/`.trash/`/`system/`, resolves unknown types to fallback, preserves the `corrections` table.

**Explicitly not in Stage 1:** any LLM call, vector store, classification, RAG.

---

## Stage 2 — Librarian Eval (the portfolio centerpiece)  ✅ core done

Full retrieval stack, intent classification, confidence scoring, RAG, groundedness, eval harness, and `LibrarianAgent` orchestration. Remaining polish items (dedup pre-write, live eval on populated vault) are non-blocking.

**Lock before building:** embedding model + chunking strategy — ✅ **DECIDED**, see `Embedding & Chunking Decision (Jul 7).md`. Summary: `gemini-embedding-2` (multimodal, one vector space for future image embedding; 8192-token input) @ 768-d (L2-normalized), prefix-based asymmetric retrieval (no `task_type` field), `sqlite-vec` `vec0` float32 brute-force KNN, chunk-native schema (whole-note = 1 chunk), Policy v1 = structured types whole-note / freeform split only above ~1000 tokens. (`-001` kept as an A/B fallback in the embed helper.)

### Retrieval  [CORE]
- [x] Vector store (`store/vector_store.py`, sqlite-vec `vec0`) — chunk-native (`chunk_id`, `note_path`, `chunk_index`, `text`, `embedding float[768]`); note-granularity scoring (collapse chunk hits to parent, min-distance). Adds a `note_paths` candidate filter for the hybrid path + a `clear()` for full rebuilds.
- [x] Embed helper (`llm/embeddings.py`) — owns the Gemini embed call + mandatory L2 renormalization + prefix/task-type, so neither can be forgotten at a call site. Offline `HashingEmbedder` fallback for no-API dev/tests.
- [x] Chunking strategy per note type (`ingestion/chunker.py`) — Policy v1 from the decision doc
- [x] Pin `sqlite-vec` (pre-v1) + `google-genai` in `requirements.txt`
- [x] **Vector indexing wired into the write pipeline** — create/update embed + (re)index, delete removes, `reindex` clears + rebuilds vectors from source-of-truth markdown alongside the metadata index. Shared note→embed-text composition (`ingestion/embed_text.py`) so the pipeline and eval harness embed identically.
- [x] `exact_lookup` module (`retrieval/exact_lookup.py`) — merged keyword + structured, no LLM, template-based response (`retrieval/templates.py`); aggregation sub-flag with strict-count-vs-tag-scan dual-check. (Keyword matches title/slug + tags; freeform-note *content* search is the semantic path's job.)
- [x] `semantic` module (`retrieval/semantic.py`) — vector search + RAG path
- [x] `hybrid` module (`retrieval/hybrid.py`) — metadata filter narrows candidates, vector search within set (accepts an already-resolved date range). Temporal-expression extraction is upstream in the classifier.

### Classification & routing  [CORE]
- [x] ~~Rule-based pre-filter for unambiguous creates~~ — **removed** (typed routing needs the classifier; plain dumps conflicted with empty-create guards).
- [x] Combined LLM classification call — intent + retrieval mode + fields/filters/target/links, one call (`classifier.py`, JSON output, offline-testable via `FakeLLMClient`)
- [x] Confidence heuristics (`classifier.py`) — `vector_margin` (top-1/top-2 gap) + `confidence_from_candidates` (target-candidate count), NOT LLM self-reported. Vector-margin applies only where vector search runs; update/delete target resolution is guarded by candidate-count.
- [~] Confidence threshold tuning against real/synthetic test cases — _thresholds live as constants (`CONFIDENCE_THRESHOLD`, `STRONG_MARGIN`); tuning needs a real case set._
- [x] Delete intent — target resolution + **mandatory confirm** (regardless of confidence) + soft-delete on affirmative (`agent._delete`)
- [x] Target resolution module (`target_resolution.py`) — explicit path → semantic search + context → recency tie-break → candidate-count confidence, shared by update and delete

### Generation & verification  [CORE]
- [x] RAG generation for semantic/hybrid (`llm/rag.generate_answer`) — answers strictly from retrieved chunk text
- [x] Groundedness check (`llm/rag.check_groundedness`) — second LLM call verifying the answer against retrieved chunks (rewrites to supported content on fail, fails open on parse error), semantic/hybrid only

### Orchestration  [CORE]
- [x] `librarian_handle` entrypoint (`agent.LibrarianAgent.handle`) — classify → route (create / exact_lookup / semantic+hybrid RAG / update / delete) → MCP-contract result (`status` / `message` / `note_id` / `action` / `pending_id`), incl. `needs_clarification` + `pending_id` confirm loop. Optional `handle(pending_id, approved)` delegates to `handle_confirm`. LLM transport in `llm/gemini_client.py` (real Gemini + `FakeLLMClient`), per-call token usage for benchmark.
- [x] Write resolution (`write_resolution.py`) — context path → explicit `target_ref` → schema identity → semantic search; redirects duplicate creates to update
- [x] Mention gate (`mention_search.py`, `link_resolution.py`) — word-boundary scan; stores pending snapshot + `pending_id` before linking
- [x] Conflict gate (`llm/update_check.py`) — LLM contradiction check before update; conflict → pending confirm
- [x] Pending confirmations (`metadata_store.pending_confirmations`, `pending_confirm.py`) — SQLite snapshot + TTL; `handle_confirm` / CLI `confirm`
- [x] CLI `handle` + `confirm` subcommands for Stage 2 manual testing

### Eval harness + benchmark  [CORE — highest portfolio ROI]
- [x] Auto-generated test question script (`eval/harness.py`) — sample N notes, a pluggable generator writes a question per note (avoiding title words), note = gold answer. `GeminiQuestionGenerator` (Flash) for real runs + offline deterministic `KeywordQuestionGenerator` so the harness runs without the API.
- [x] Scoring — recall@k + MRR / whether gold note lands in top-k; works against any retriever with `.search(query, k)` (semantic or hybrid). Wired into the CLI: `python -m librarian.cli eval --path {semantic,hybrid} -k N`.
- [~] Run against 15-20+ auto-generated cases, plus the Stage 1 smoke tests re-run through the full pipeline — _harness + CLI ready; a real multi-note run pending a populated vault._
- [x] Log tokens per request, per path — `benchmark/tokens.py`: `TokenTracker` + `MeteredLLMClient` buckets every LLM call by phase (classify / generation / groundedness). Real `usage_metadata` from Gemini when available, chars/4 estimate offline.
- [x] **Token A/B benchmark** (`benchmark/ab.py`) — 10 representative requests across create/update/delete/exact/semantic/hybrid. **Arm A** = full `LibrarianAgent` (classify → structured/RAG). **Arm B** = passthrough model (one in-context call over the whole vault). Report: per-request tokens, totals, B/A ratio, Arm A phase concentration, avg by kind. CLI: `python -m librarian.cli benchmark [--arms A,B] [--no-seed]`. _Live numbers need a real Gemini key; offline harness is tested._

### Vault graph  [CORE]
- [x] LLM-suggested wikilinks at ingestion (1-2 links, same classification call, no extra cost) — classifier extracts `links`; `agent._create` writes them to frontmatter.

### Polish (ship after core paths work)  [CORE-ish, not blocking]
- [x] Aggregation sub-flag on `exact_lookup` — dual-check (strict count + tag scan) + discrepancy surfacing (`exact_lookup._aggregate` + `templates.render_aggregation`)
- [ ] Dedup check pre-write — vector similarity vs existing notes on create, threshold-gated `needs_clarification` on near-dupes

### Learning over time  [split]
- [x] **[CORE]** Correction logging on write — classifier sets `is_reaction` for pushback; `log_correction` runs after a successful update when `is_reaction` or an approved conflict pending.
- [ ] ~~PA turn-adjacency tracking~~ — dropped, replaced by explicit trigger above

**Explicitly not in Stage 2:** multi-intent splitting, reranking, GraphRAG/LightRAG, PA wiring, and all [v2] learning items below.

---

## Stage 3 — PA

Hermes PA profile, Telegram gateway, Librarian wired in as an MCP server. Intentionally last (see Guiding principles). Depends on Stage 2 being independently solid.

- [ ] Hermes profile setup, Gemini Flash connected, built-in memory (default — Honcho deferred, config-only upgrade if revisited)
- [ ] Telegram gateway
- [ ] Core reminders (PA-only, no vault) — one-off, recurring, birthday feed-in
- [ ] Habits — interval-based recurring reminder + snooze loop (PA-side: `frequency` drives the interval, "later" snoozes by `snooze_duration`, re-fires until done or next interval). On "done", update the habit note's stats via the Librarian. **Finalize the "on time" definition here** (leading candidate: on-time = done on the first reminder of the interval, no snooze). Habit *definition + stats storage* already works via the Stage 1 write pipeline; this item is only the PA reminder loop + stat-increment logic.
- [ ] Vault-related routing — binary classify (vault-related or not) + PA-side message splitting for mixed intents
- [ ] Wire `librarian_handle` + `librarian_confirm` as MCP tools (stdio server), including `pending_id` button loop
- [ ] Wire `librarian_query_raw` for system-triggered reads (birthday cron, quiz batch-fetch)
- [ ] Voice input — transcription → text → normal PA pipeline
- [ ] Image input — Gemini Flash vision caption → text → normal PA pipeline
- [ ] Idempotency on PA retry (prevent double-create on retried calls)
- [ ] Friends CRM — profile save/query, birthday cron, flashcard quiz
- [ ] Task tracking (low priority)
- [ ] Growth journal — wins, learnings (lowest priority)

---

## v2 — needs real longitudinal data (designed now, built later)

These are fully designed in Architecture.md and are good resume material *as designed-and-scoped decisions*. They are **not** built for submission because they can't be meaningfully validated without months of accumulated usage — building them early would produce unfalsifiable dead code.

- [ ] **Preference/pattern synthesis cron** — weekly/monthly LLM pass over high-strength memories, writes a profile note of inferred preferences. Needs weeks of real notes + corrections first.
- [ ] **Retrieval personalization** — weight semantic/hybrid search toward the synthesized profile on borderline-confidence matches. Depends on preference synthesis being live and producing a usable profile.
- [ ] **Ebbinghaus decay — ranking effects** — daily decay tick + tier transitions. Scope guard (see Architecture): decay only affects rollup/summarization and ranking tie-breaks, **never** removes a note from the candidate set. The tick itself is cheap to add anytime; its *value* only shows with real history.
- [ ] Weekly schema clustering cron — flag emerging note-type patterns (e.g. `media`, `growth`) for approval.
- [ ] Multi-intent handling (single message, multiple intents) — list-based classification + sequential execution.
- [ ] Revisit: Honcho adoption, if Hermes built-in memory proves insufficient.
- [ ] Revisit: f-string template alternative for `exact_lookup` responses.
- [ ] Revisit: intent taxonomy rigidity — possible `secondary` intent field.
- [ ] Video ingestion (stretch goal, no auto-understanding planned).
