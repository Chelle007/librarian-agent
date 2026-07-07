# Build Plan

> Last updated: July 2026
> Status: Active
> See Architecture.md for full rationale behind each item below.

---

## Stage 1 — Librarian Baseline

Vault I/O, schema validation, metadata store, structured queries, CLI test harness. No LLM involved. Goal: working, demoable vertical slice, submittable as a portfolio checkpoint.

- [ ] Vault I/O module (`store/vault_io.py`) — read/write markdown + frontmatter
- [ ] `.raw/` immutable write-through on every ingest (date-archived)
- [ ] `schema.json` loader + validator (`store/schema.py`)
- [ ] Metadata store (`store/metadata_store.py`) — SQLite table: type, path, tags, created_date, last_modified
- [ ] Write pipeline: frontmatter build → schema validate → file write → metadata upsert → git commit/push
- [ ] Folder routing by `type` (deterministic, no LLM)
- [ ] Generic fallback bucket (`notes/`, `type: note`) for unmatched content
- [ ] `.trash/` soft-delete mechanism (move, not `rm`) — even without full delete-intent classification yet, build the primitive now
- [ ] CLI test harness — manual create/update/query/delete calls against the pipeline directly
- [ ] `librarian_query_raw(filter)` as a real tool (not just dev harness) — structured filter, no LLM
- [ ] 5–8 case smoke test covering: create (each type), fallback-bucket create, structured query, soft-delete
- [ ] `corrections` table in metadata store (`original_classification, corrected_to, note_id, timestamp`) — log every revert-via-reply event; signal is lost if not captured at the moment it happens, so this can't wait for Stage 2

**Explicitly not in Stage 1:** any LLM call, vector store, classification, RAG.

---

## Stage 2 — Librarian Eval

Full retrieval stack, intent classification, confidence scoring, RAG, dedup, groundedness, eval harness. Complete when the Librarian is independently solid and proven correct — PA depends on this being done first.

### Retrieval
- [ ] Vector store (`store/vector_store.py`, sqlite-vec) — embeddings per note/chunk
- [ ] Chunking strategy per note type (`ingestion/chunker.py`) — **lock embedding model + chunking strategy before building this**, changing either later requires re-embedding everything
- [ ] `exact_lookup` module (`retrieval/exact_lookup.py`) — merged keyword + structured, no LLM, template-based response
- [ ] `semantic` module (`retrieval/semantic.py`) — vector search + RAG generation
- [ ] `hybrid` module (`retrieval/hybrid.py`) — metadata filter narrows candidates, vector search within set, + temporal expression extraction/date-range filter, + RAG generation
- [ ] Aggregation sub-flag on `exact_lookup` — dual-check (strict count + tag scan) + discrepancy surfacing (treat as polish, can ship after core paths work)

### Classification & routing
- [ ] Rule-based pre-filter for unambiguous creates (skip LLM entirely)
- [ ] Combined LLM classification call — intent + retrieval mode, one call (`classifier.py`)
- [ ] Confidence heuristics (`classifier.py`) — vector margin + coreference/target-candidate-count, NOT LLM self-reported score
- [ ] Confidence threshold tuning against real/synthetic test cases
- [ ] Delete intent — target resolution + mandatory confirm + soft-delete on confirm
- [ ] Target resolution module (`target_resolution.py`) — context → recency heuristic → clarification, shared by update and delete

### Generation & verification
- [ ] RAG generation for semantic/hybrid (`llm/gemini_client.py`)
- [ ] Groundedness check — second LLM call verifying generated answer against retrieved chunks, semantic/hybrid only
- [ ] Dedup check pre-write — vector similarity vs existing notes on create, threshold-gated `needs_clarification` on near-dupes

### Vault graph
- [ ] LLM-suggested wikilinks at ingestion (1-2 links, same classification call, no extra cost)

### Eval harness
- [ ] Auto-generated test question script — sample N notes, LLM writes a question per note (avoiding title words), note = gold answer
- [ ] Scoring — recall@k / whether gold note lands in top-k, per retrieval path
- [ ] Run against 15-20+ auto-generated cases, plus the Stage 1 smoke tests re-run through the full pipeline
- [ ] Log tokens per request, per path — feeds the A/B benchmark below

### Learning over time
- [ ] Preference/pattern synthesis cron — weekly/monthly pass over high-strength (core/active tier) memories, LLM writes a profile note of inferred preferences/patterns, scoped to vault-content patterns only (not conversational — that's Hermes/Honcho's job if adopted later). Needs real accumulated usage first — don't build/run before Stage 1 has been live a while.
- [ ] `/correct_librarian` command (or NL equivalent) — PA forwards verbatim to Librarian as an explicit correction; Librarian's classification call tags `reaction` only in this explicit case, logged against `corrections` table
- [ ] ~~PA turn-adjacency tracking~~ — dropped, replaced by explicit trigger above

**Explicitly not in Stage 2:** multi-intent splitting, reranking, GraphRAG/LightRAG, PA wiring, retrieval personalization (needs Stage 2 profile synthesis first), Honcho setup (deferred to Stage 3, default to Hermes built-in memory).

---

## Stage 3 — PA

Hermes PA profile, Telegram gateway, Librarian wired in as an MCP server. Depends on Stage 2 being independently solid.

- [ ] Hermes profile setup, Gemini Flash connected, built-in memory (default — Honcho deferred, config-only upgrade if revisited)
- [ ] Telegram gateway
- [ ] Core reminders (PA-only, no vault) — one-off, recurring, birthday feed-in
- [ ] Vault-related routing — binary classify (vault-related or not) + PA-side message splitting for mixed intents (e.g. reminder + vault save in one message)
- [ ] Wire `librarian_handle` as MCP tool call, including `needs_clarification` multi-turn loop
- [ ] Wire `librarian_query_raw` for system-triggered reads (birthday cron, quiz batch-fetch)
- [ ] Voice input — transcription → text → normal PA pipeline
- [ ] Image input — Gemini Flash vision caption → text → normal PA pipeline
- [ ] Idempotency on PA retry (prevent double-create on retried calls)
- [ ] Friends CRM — profile save/query, birthday cron, flashcard quiz
- [ ] Task tracking (low priority)
- [ ] Growth journal — wins, learnings (lowest priority)

---

## Post-Launch / Ongoing

- [ ] **Token A/B benchmark**: PA+Librarian+Obsidian vs PA+direct-Obsidian-MCP, 10-15 representative requests across all paths, log and compare token cost + where cost concentrates
- [ ] **Retrieval personalization**: weight semantic/hybrid search toward the synthesized profile note on borderline-confidence matches. Depends on Stage 2 preference synthesis being live and producing a usable profile first.
- [ ] Revisit: Honcho adoption, if Hermes built-in memory proves insufficient
- [ ] Weekly schema clustering cron — flag emerging note-type patterns for approval
- [ ] Ebbinghaus daily decay tick
- [ ] Multi-intent handling (deferred from Stage 2) — list-based classification + sequential execution
- [ ] Video ingestion (deferred, stretch goal)
- [ ] Revisit: f-string template alternative for `exact_lookup` responses
- [ ] Revisit: intent taxonomy rigidity — possible `secondary` intent field
