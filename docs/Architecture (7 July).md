# Personal AI Assistant — System Architecture

> Last updated: July 2026
> Status: Revised design, pre-build — Baseline-first build order
> Librarian implementation language: Python

---

## Overview

A two-agent personal AI assistant: a thin PA Agent built on Hermes Agent (handling Telegram, conversation, reminders, cron, voice), and a Librarian Agent built from scratch as a standalone MCP server (owning the entire Obsidian vault — retrieval, schema enforcement, and writes). The two communicate over a synchronous MCP tool call, not a task queue. The Librarian is built and validated first in isolation, then eval'd, before the PA is wired in.

This document folds in decisions made during the Librarian architecture design session (July 2026), on top of the original system design.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| PA framework | Hermes Agent (Nous Research) | Native Telegram gateway, cron, MCP client support |
| Librarian | Custom MCP server, Python | Owns retrieval strategy, schema enforcement, and vault writes |
| LLM | Gemini Flash | Cost-efficient, swappable; hard constraint — no Pro upgrade |
| Knowledge base | Obsidian (markdown vault) | Local-first, plain markdown, no vendor lock-in |
| Vector store | sqlite-vec (or equivalent) | Embeddings per note/chunk for semantic search |
| Metadata store | SQLite table | Structured filtering (type, date, tags) |
| Interface | Telegram (text + voice), via PA only | Single user-facing surface |
| Hosting | Hetzner CX22 VPS (~$4.59/mo) | Flat pricing, single box runs PA, Librarian, vault index |
| PA ↔ Librarian transport | MCP over stdio | Co-located on the same VPS; no network exposure needed |
| Vault sync | obsidian-git (desktop auto-push) + VPS auto-commit/push + VPS cron pull | Bidirectional |

---

## Two-Agent Design

### Why two agents

The PA needs no knowledge of vault internals — it only needs to recognize "this is vault-related" and forward the raw request. Isolating the vault's retrieval logic, schema, and write behavior in a separate service keeps PA's context small, and lets the Librarian be built, tested, and evaluated entirely independently of Hermes.

**North star: code sustainability > accuracy = token efficiency = latency.**

Code sustainability leads because this is a portfolio piece and a tool the author must maintain solo over time — clean, readable, testable, deterministic-where-possible code that a reviewer can follow is worth more than shaving tokens or milliseconds. Accuracy, token efficiency, and latency then rank equally: at ~10 requests/day on Gemini Flash the dollar cost is negligible, so token efficiency is pursued as a *design discipline and portfolio narrative* (see the A/B benchmark), not as an optimization that should ever be traded against correctness or clean code.

### Agent 1 — PA Agent (Hermes)

- **Framework:** Hermes Agent, single profile
- **Model:** Gemini Flash
- **Gateway:** Telegram (sole user-facing interface)
- **Role:** Conversational orchestrator. Classifies whether a message is vault-related; if so, forwards raw text + recent context to the Librarian via MCP and relays the result back verbatim. Otherwise handles directly (reminders, voice transcription, cron).
- **Does NOT have:** vault schema knowledge, retrieval logic, direct Obsidian access, or any awareness of Librarian's internal feature set — Librarian doesn't need PA context and PA doesn't need Librarian internals.
- **Does need:** minimal self-awareness of its own feature boundary — enough to split a mixed message ("remind me to call mom, and log I cleaned my room") into a PA-handled part and a Librarian-forwarded part *before* calling `librarian_handle`. This splitting is a PA-side responsibility, not something solved by injecting PA context into the Librarian.
- **Image input:** runs Gemini Flash vision captioning on received images, then treats the resulting caption as raw text through the normal classification pipeline (same pattern as voice transcription). Video input is out of scope for v1 (no cheap "describe this video" primitive at this budget) — stored as an attachment with a user-provided caption only, no auto-understanding. Stretch goal, same tier as phone alarms.

### Agent 2 — Librarian Agent (custom, MCP server, Python)

- **Framework:** Built from scratch, exposed as an MCP server
- **Model:** Gemini Flash — one combined intent + retrieval-mode classification call per request; separate call for RAG generation on semantic/hybrid paths
- **Gateway:** None — never addressed by the user directly
- **Role:** Cataloging and retrieval service for the entire vault. Routes each request, resolves targets, validates against schema, writes, generates grounded responses for content queries.
- **Only has:** vault read/write, vector store, metadata store, schema validation logic. Zero awareness of PA's feature set — this is deliberately one-directional.

---

## Agent Communication — MCP Tool Call

### Contract

Two MCP tools (Stage 3 wiring; callable from CLI today):

**`librarian_handle`** — anything that originates as user free text:

```
librarian_handle(
  raw_request: string,           // verbatim user text, not PA-paraphrased
  context: string | null,        // recent conversation turns, for coreference
  pending_id: string | null,     // optional: resume a pending confirmation
  approved: bool | null,         // required when pending_id is set
) -> {
  status: "done" | "needs_clarification" | "error",
  message: string,
  note_id: string | null,
  action: "created" | "updated" | "deleted" | "queried" | null,
  pending_id: string | null      // set when user must approve (PA buttons)
}
```

**`librarian_confirm`** — approve/reject a stored pending action (mention link,
conflict overwrite, delete). Equivalent to `librarian_handle(..., pending_id, approved)`.

When `status: needs_clarification` with a `pending_id`, PA shows the message and
confirm/cancel buttons, then calls `librarian_confirm` — not a free-text "yes"
through classification. PA should still relay `context` on normal follow-ups
(coreference, factual corrections).

Pending snapshots live in SQLite (`pending_confirmations`); TTL 24h default, 1h for delete.

A second tool, `librarian_query_raw(filter)`, is promoted from dev-only harness to a real production tool — used for **system/cron-triggered structured reads** that were never ambiguous in the first place (e.g. birthday-check cron reading a contact's birthday field, flashcard quiz batch-fetching contacts). These are known-structure, non-freeform triggers and shouldn't burn an LLM classification call.

**Rule of thumb on tool granularity:** `librarian_handle` stays the single entrypoint for anything that originates as user free text — that's where the Librarian's classification intelligence lives, and is the whole point of not leaking schema/routing knowledge back into PA. Fine-grained per-feature write tools (`librarian_add_contact`, etc.) were considered and rejected — they'd shift classification responsibility back onto PA implicitly. `librarian_query_raw` is the one deliberate exception, reserved for structured, system-triggered reads only.

---

## Intent Classification & Confidence

### Intents (4, not 3 — delete added)

`create | update | query | delete` (+ `ambiguous` fallback)

### Confidence scoring — heuristic-based, not LLM self-reported

LLM self-reported confidence was tested and found unreliable — it clusters near 0.9 regardless of actual correctness. Confidence is instead computed from two cheap, deterministic heuristics:

1. **Vector margin** — gap between top-1 and top-2 similarity scores from vector search. Small margin (e.g. top-1: 0.89, top-2: 0.87) → ambiguous target → low confidence. Large margin (0.91 vs 0.61) → confident.
2. **Coreference/target resolution count** — checks how many plausible candidates target resolution finds for pronouns/references ("update it," "rate that"). Exactly one candidate → confident. Zero or multiple → low confidence.

Both are arithmetic/logic on data already available (no extra LLM call). Below a tuned confidence threshold → auto-route to `needs_clarification`.

**Known limitation:** heuristics catch *retrieval* ambiguity (multiple plausible matches) but not cases where a single match is confidently *wrong* — high similarity doesn't guarantee semantic correctness. Accepted tradeoff for the cost savings at this scale.

**Gemini logprobs** were considered as an alternative/supplement (real math: margin between top-2 token logprobs) but support has been reported as inconsistent across specific Gemini model versions — not adopted for v1, flagged as a possible future enhancement if calibration becomes a real problem.

### Delete flow (new — was missing from original design)

```
DELETE → target resolution (same mechanism as update)
       → ALWAYS needs_clarification with explicit confirm, regardless of confidence
         ("delete note X? this can't be undone via chat")
       → on confirm: soft-delete — move to .trash/ (or tag deleted: true)
         never a hard rm
       → git commit + push
```

Soft delete keeps blast radius low even if target resolution picks the wrong note — recoverable either way.

### Create / update hardening (Stage 2)

Before any vault write from free text:

1. **Write resolution** (`write_resolution.py`) — resolve target in order: conversation
   context path → explicit `target_ref` → schema identity (same contact name, etc.) →
   semantic search. Redirects "create" to "update" when identity already exists.
2. **Mention gate** — if an identity label appears in other vault notes, store a
   pending snapshot and return `pending_id` (user confirms wikilink merge).
3. **Conflict gate** — LLM checks whether the proposed update contradicts existing
   content; contradictions → pending confirm before overwrite.
4. **Correction logging** — after successful update, log to `corrections` when
   `is_reaction` or approved conflict pending (see Learning Over Time).

### Multi-intent (create + update in one message) — deferred

Real design gap, explicitly deferred past v1. Planned approach when revisited: classification returns a *list* of intent objects instead of one; router executes each sequentially through its normal pipeline; `librarian_handle` aggregates results into one `message`. Risk: asking one LLM call to segment + classify + route multiple sub-requests will likely reduce per-segment reliability — treat single-intent as the v1 correctness bar, add multi-intent only after Stage 2's eval set is passing, since every multi-intent case effectively multiplies the eval set.

**Open concern (flagged, not resolved):** the 4-bucket rigid taxonomy still feels constraining for edge cases near intent boundaries. Possible future softening: optional `secondary` intent field for near-boundary cases, without going to full open-ended classification (which would break deterministic routing and eval scoring).

---

## Retrieval Taxonomy

**Conceptual taxonomy: 5 paths** (kept for classification/routing labels and portfolio narrative — this precision is a real differentiator vs. comparable projects, which mostly do "keyword, vector, or a fusion of both").

**Implementation: 3 modules** (collapsed for lower build/maintenance surface — keyword and structured are both non-LLM SQLite lookups differing only in which field is matched, so they share one module):

| Conceptual path | Implementation module | LLM involved | Generation |
|---|---|---|---|
| No retrieval | `exact_lookup` (create branch) | No | No |
| Keyword / exact match | `exact_lookup` | No | Template |
| Structured / metadata | `exact_lookup` | No | Template |
| Semantic | `semantic` | Yes | RAG |
| Hybrid | `hybrid` | Yes | RAG |

**Aggregation queries** are a sub-flag within `exact_lookup` (structured), not a separate top-level path. Aggregation-flagged queries additionally run a dual-check: strict structured count (matching `type`/`subtype`) plus a tag-based scan, and surface any discrepancy transparently instead of silently returning a partial count — e.g. *"Found 12 books by type, but 3 more notes are tagged 'book' — include them?"* This exists because schema-on-read's generic fallback bucket means a strict `type`-only count can quietly undercount. Treated as Stage 2 polish, not a Stage 1 blocker.

**Temporal fuzziness in hybrid:** the classification call additionally extracts relative time expressions ("a while back," "last month") and converts them into an actual date range, layered as a metadata filter alongside the vector search — not left as unencoded vibes in the embedding.

**Reranking:** explicitly out — at personal-vault scale, gains matter most retrieving from 20+ candidates; not the case here.

**Groundedness verification:** ADDED. A second LLM call on semantic/hybrid paths only, checking the generated answer against retrieved chunks before returning (pass/fail or rewrite-to-supported-content). Not applied to `exact_lookup` since there's no generation to hallucinate there.

**Confirm-before-write (LLM re-verification of placement):** considered, rejected. Folder placement is a deterministic lookup from `type` (defined in `schema.json`) — the only way it goes wrong is if classification itself picked the wrong type, which is already covered by confidence-gated clarification. A second LLM call re-checking classification's own output is redundant; better lever is tightening the confidence threshold upstream.

**Response generation for `exact_lookup`:** template-based (f-string), not LLM-generated. E.g. `"Last movie you watched: {title} (rated {rating}, watched {date})"`.

**Open concern (flagged, not resolved):** dissatisfaction with f-string templates persists — wants to explore an alternative rendering approach later. Note: prior template bugs were traced to incorrect underlying query data, not the templating mechanism itself, but the preference to revisit stands.

---

## Obsidian Vault Structure

```
vault/
├── .raw/              # Immutable audit trail — original user input, archived by date, never deleted
├── .trash/            # Soft-delete destination — never a hard rm
├── notes/             # Processed ideas and information     (type: note — generic fallback bucket)
├── contacts/          # Friends CRM profiles                (type: contact)
├── tasks/             # Task tracking                       (type: task)
├── habits/            # Habit definitions + completion stats (type: habit)
├── inbox/             # Heartbeat outputs, daily briefs     (type: brief)
└── system/
    ├── schema.json    # Single source of truth for note types and required fields — evolves on approval
    └── MOC/           # Auto-generated Maps of Content
```

**Formalized types track `schema.json`** (the single source of truth): `note`, `contact`, `task`, `habit`, `brief`. Types floated in the original design but **not yet formalized** — `media` (movies/books/games) and `growth` (wins/learnings/achievements) — are intentionally left out of `schema.json` for v1; they land in `notes/` via the schema-on-read fallback until the weekly clustering pass promotes them to formal types on approval. Don't pre-create their folders.

### Habits — ownership and completion tracking

Habits are a **split responsibility**: the Librarian owns the habit *definition* and its accumulated *stats* (vault content); the PA owns the *reminder scheduling and snooze loop* (a reminder/cron behavior, the PA's job). The PA reads a habit via `librarian_query_raw` to know its interval, and writes back a "done" event through `librarian_handle` (or a system-triggered path) to update stats. This resolves the earlier ambiguity in favor of: **definition + stats = Librarian, live reminder loop = PA.**

**Reminder + snooze behavior (PA):** a habit recurs on a fixed interval (e.g. "every 8h"). Each interval the PA fires the reminder. If the user responds "later," the PA snoozes by `snooze_duration` (default ~10–15 min) and re-fires, repeating until the habit is marked done or the next interval arrives. The interval schedule and snooze timers live in the PA's cron/reminder layer, not the vault.

**Habit fields (frontmatter, stats are derived counters):**

- `frequency` (required) — recurrence interval, e.g. `8h`, `daily`, `weekly`. This is what the PA schedules against.
- `snooze_duration` (optional) — how long "later" pushes the next nudge; defaults to a global ~10–15 min if unset. Per-habit override.
- `completions_total` / `completions_on_time` — counters answering "how many times have I done this, and how many on time".
- `current_streak` / `longest_streak` — consecutive on-time completions.
- `last_completed` — timestamp of most recent completion, used to detect a missed interval and reset `current_streak`.

**"On time" definition (proposed — confirm):** for each occurrence, *on time* = marked done on the **first** reminder of that interval (no "later"/snooze); *late* = done only after one or more snoozes. This directly measures "did I do it when first nudged." Determined at the moment the "done" event is processed, from whether the current occurrence was ever snoozed — a runtime/PA concern, not a schema one.

Counters are lossy-but-cheap by design; per-occurrence history (exact snooze counts, which intervals were missed) isn't stored in frontmatter, but each raw input is preserved in `.raw/` if a full recompute is ever needed.

### `.raw/` — clarified

`.raw/` is **not** a chat/conversation log — it's a snapshot of each individual raw input at ingestion time, with no threading between entries. It does not substitute for the `context` parameter (recent conversation turns), which is still required for coreference resolution. Its purpose: recoverability (if parsing/classification gets something wrong, the original is never lost) and enabling revert/edit-via-reply against a known-good original rather than an already-transformed note. Entries are never removed — cheap to keep forever; archive by date subfolder if the vault ever grows large enough to matter.

---

## Schema Evolution (schema-on-read approach)

Unchanged from original design — unknown content → generic `notes/` bucket with `type: note` and freeform tags, no blocking. Weekly clustering pass flags patterns (tag frequency + optional embedding similarity); user approves before `schema.json` is updated with a new formal type. Schema is explicitly updatable over time — this mechanism is the whole point, not a fixed pre-build schema.

---

## Wikilinks & Graph Strategy

**Original assumption invalidated:** the design assumed the user hand-curates `[[wikilinks]]` while using Obsidian normally. Since interaction is Telegram-only and Obsidian may never be opened, that assumption doesn't hold — wikilinks would never get created without a mechanism inside the Librarian itself.

**Adopted fix:** the ingestion-time classification LLM call additionally suggests 1–2 `links` based on note content — same call, near-zero marginal cost. This moves from "nice optimization" to "required," since manual linking is no longer a realistic fallback.

**GraphRAG (full entity extraction + community detection + graph traversal) — evaluated and rejected.**
- Cost: full GraphRAG indexing runs roughly $0.30+ per ~30–40K words even with cheaper models, and industry guidance is that its overhead doesn't pay off under ~100,000 tokens of corpus — well above what a personal vault will contain for a long time. It also requires standing up a graph database, community detection, and a dedicated extraction pipeline — real infrastructure for a single-user project.
- What it would unlock that suggested-links don't: **inferred** relationships never explicitly written (e.g. "friends who like movies I rated highly" — connecting a contact note and a media note with no wikilink between them). At personal-vault scale, the user is also the author, so most such connections are known but simply not always linked — a discoverability gap, not a missing-knowledge gap.
- **LightRAG** (a cheaper GraphRAG variant that skips the expensive community-summarization step, using dual-level local/global retrieval directly over the graph) was noted as existing and dramatically cheaper — but still adds a graph-store dependency the vault doesn't currently need. Not adopted; same rationale as full GraphRAG, just a smaller version of the same unnecessary complexity at this scale.
- **Decision: stay with wikilinks + LLM-suggested links.** Revisit only if the vault grows well past personal-scale or relationship-heavy queries become a real, frequent need.

---

## Agent Frameworks & Orchestration — Evaluated and Rejected

Three adjacent approaches were considered for building the Librarian (and, by extension, the PA) and rejected for v1. Documented here for the same reason GraphRAG/reranking are documented above: preserve the reasoning, don't relitigate later, and it's honest portfolio material — shows deliberate evaluation, not default-avoidance.

**LangChain** — rejected. Would replace direct Gemini SDK / instructor / tenacity calls with framework wrappers (LLM abstraction, prompt templates, chains). Offers no benefit here: model is hard-locked to Gemini Flash (no multi-provider need), and the differentiated logic (classification, confidence heuristics, schema validation) has no LangChain primitive to slot into — it would sit alongside custom code, not replace it, adding a dependency without removing complexity.

**LangGraph** — rejected for now, re-evaluate later. More relevant than LangChain since Librarian genuinely has multi-step flow, but current flow (classify → route to 1-of-4 intents → retrieve/write → optional verify → respond) is small, fixed-shape, and doesn't branch dynamically at runtime — a dict/match dispatch covers it in a handful of lines with a normal Python stack trace on failure. Comparable project `ObsidianRAG` (Vasallo94) does use LangGraph for its Q&A path, but its pipeline is denser (hybrid search + reranking + GraphRAG link-expansion, both explicitly rejected here) and multi-provider (Ollama/LM Studio/OpenAI-compatible) — neither condition holds for this project yet.

*Watchlist — re-evaluate LangGraph if any of these become true:*
- Multi-intent handling (deferred, see Intent Classification section) grows from a simple list-and-loop into real branching/sub-agent coordination
- Retrieval pipeline gains multiple conditional stages (rerank, graph-expand, multi-pass retry) rather than the current 3-module split
- A genuine need emerges for resumable/checkpointed execution (pause mid-flow, replay a run)
- Orchestration logic in any one module becomes hard to reason about as plain Python (not "would look more impressive," but "actually hard to trace")

**Skill-based architecture** (markdown instruction files read by a general-purpose coding agent at trigger time — e.g. Karpathy's "LLM Wiki" pattern, seen in projects like `claude-obsidian`, `obsidian-wiki`) — rejected. This pattern replaces deterministic code with per-run LLM improvisation guided by a prompt file: no schema validator, no confidence-heuristic scoring, no repeatable eval harness, since behavior isn't fixed code. Conflicts directly with locked decisions here (schema-on-read + Pydantic validation, heuristic — not LLM-self-reported — confidence, recall@k eval harness assuming deterministic retrieval). Also assumes an interactive coding-agent session as the interface, whereas this project's interface is a headless MCP server behind a Telegram bot. Legitimate only as a tool for building Librarian faster (e.g. a personal dev skill), never as the runtime architecture.

---

## Data Layer

Unchanged — two SQLite-backed stores: vector store (sqlite-vec, embeddings per note/chunk) and metadata store (plain SQLite table: type, created_date, last_modified, tags, path). Vault markdown+frontmatter remains source of truth; the index is a derived, rebuildable cache (rebuilt via `librarian.cli reindex`).

**Embedding + chunking locked (see `Embedding & Chunking Decision (Jul 7).md`):** `gemini-embedding-2` (multimodal) at 768 dims (Matryoshka-truncated, L2-normalized), asymmetric retrieval via text prefixes (`-2` has no `task_type` field), `sqlite-vec` `vec0` float32 brute-force KNN. The vector store is **chunk-native** — a whole note is just a 1-chunk note — so a future chunking-policy change is a re-embed only, never a schema migration. **Why `-2` over `-001`:** an embedding model defines a vector space, so putting text and future image embeddings in the *same* multimodal space is the only way cross-modal retrieval works later — starting on `-2` avoids a forced full re-embed the day images are added. Bonus: 8192-token input (less chunking), auto-normalization. `-001` is retained in the embed helper as a text-only A/B fallback. Rationale for API-over-local: no new privacy exposure (content already transits Gemini), zero ML infra on the 4 GB VPS, negligible cost — consistent with the code-sustainability north star.

---

## Learning Over Time (added — post-planning-session, July 2026)

New goal identified: system should improve the more the user interacts with it, not just retrieve accurately. Distinct from retrieval quality — this is a stateful learning-over-time concern, addressed with three converging layers, sequenced by data dependency (later layers need data only earlier layers produce):

### 1. Correction logging (Stage 1 table, Stage 2 write hook)

`corrections` table in the metadata store: `note_id, original_classification, corrected_to, timestamp`.
`original_classification` holds a JSON before-snapshot of the note (frontmatter + body).

**Stage 2 behavior:** after a **successful vault update**, log a row when:
- the classifier sets `is_reaction` (user is correcting/revising prior librarian output), or
- the user approved a **conflict** pending (overwrite of contradictory content).

Factual corrections (e.g. "desmond is a guy, not a girl") route as `update` and both
fix the vault and log — there is no log-only shortcut. Casual elaboration (`is_reaction=false`)
does not log.

### 2. Preference/pattern synthesis (Stage 2/3)
Periodic (weekly/monthly) cron pass — same pattern as the existing schema-clustering cron — reads recent high-strength (core/active tier, per Ebbinghaus decay) memories and writes an explicit profile note (inferred preferences, recurring themes). This is what produces the actual "it knows me" feel; correction logging alone doesn't. Requires weeks of accumulated notes + corrections to be worth running — cannot usefully start before real usage exists.

### 3. Retrieval personalization (post-launch)
Once a profile note exists, weight semantic/hybrid search toward it (e.g. boost candidates matching known interests when confidence is borderline). Cheap once #2 exists, meaningless without it.

**Explicitly deferred / not adopted:** reaction logging (logging casual affirmations like "oh nice, I like how you linked that") was considered as a broader signal source beyond corrections. Flagged as too noisy/ambiguous to scope now — casual affirmation vs. genuine signal about *what specifically* was good isn't reliably extractable from short reactions. Possible future narrowing: log reactions only on wikilink suggestions and classification choices specifically (where there's something concrete to tune), not all conversational affect. Not added to Build Plan until scoped further.

**Relationship inference (GraphRAG-adjacent):** the "connections never explicitly written" gap (see GraphRAG section) is the natural 4th layer here, but stays rejected for the same cost/scale reasons — revisit only after layers 1-3 are running and that specific wall is actually hit.

### PA → Librarian correction signal

Turn-adjacency was considered and rejected (false positives). **Adopted:** classifier
sets `is_reaction` when the user is pushing back on prior output; the normal mutate
path runs and `log_correction` fires only after a successful write. No separate
log-only route. PA forwards correction utterances verbatim with `context` for target
resolution — no PA-side judgment beyond relaying text.

### Conversational memory: Hermes built-in vs Honcho
Hermes ships built-in cross-session memory (MEMORY.md/USER.md) with no extra setup. Honcho is an optional memory provider plugin (external service, honcho.dev) that adds per-turn dialectic reasoning — a deepening model of user preferences/patterns derived from conversation. **Decision: default to Hermes's built-in memory for v1**, skip Honcho — avoids an extra external dependency before it's proven necessary, consistent with the self-hosted/no-lock-in philosophy. Honcho remains a config-only upgrade (`memory: provider: honcho`) to revisit later if built-in memory feels insufficient. Not an architecture-defining choice either way — deferred to a Stage 3 config decision, not a pre-build blocker.

**Boundary, for when Honcho (or built-in memory) is evaluated:** conversational memory (Hermes/Honcho) models what the user says in chat; Librarian's vault-content synthesis (see below) models patterns only visible in accumulated vault content (ratings, corrections, note types) that were never said aloud. Different data sources — not redundant, don't merge.

---

## Ebbinghaus Memory Decay

Unchanged from original design. Strength decays over time, grows with access; tiers `core → active → warm → cold → archive`; daily decay tick owned by Librarian's internal cron. Cold/archived high-volume content (e.g. future diary entries) can be periodically rolled up into summaries.

**Scope guard (added):** a *second brain's* core value is that it never silently forgets — so decay must never remove a note from the retrieval candidate set or hide it from a query. Decay only affects (1) **rollup/summarization** of high-volume cold content and (2) **ranking tie-breaks** among already-retrieved candidates. A 6-month-old note is always still findable; decay can lower where it sorts, never whether it appears. This keeps decay from turning into a recall regression.

**Prior art note:** `agent-second-brain`'s `autograph` memory layer independently converged on the same decay formula shape and same five-tier naming — worth citing explicitly as validating prior art in any writeup, not coincidence.

---

## Vault Sync

Unchanged — bidirectional via obsidian-git (desktop push) + VPS auto-commit/push + VPS cron pull. Last-write-wins, acceptable at single-user scale.

---

## Eval Harness (Stage 2) — auto-generated test set

Adopted pattern (also used by `obsidian-second-brain`):
1. Sample N notes from the vault.
2. For each, prompt an LLM: *"write a question this note answers, without reusing its title words"* — forces testing of semantic retrieval, not string matching.
3. The sampled note becomes the gold answer.
4. Run the retrieval pipeline against the generated question, score recall@k / whether the gold note lands in top-k.

Self-updating eval set that grows with the vault, rather than a fixed hand-written 15–20 cases. Doubles as both correctness proof and portfolio material ("built a retrieval eval harness that bootstraps its own test cases from the corpus").

**Live run (Jul 10, 2026)** — real vault (~108 notes), Gemini question generator, `n=20`, `k=5`, `seed=0`:

| Path | recall@5 | MRR |
|---|---|---|
| semantic | 100% (20/20) | 1.000 |
| hybrid | 100% (20/20) | 0.963 |

Both paths retrieved the gold note in top-5 for every case; semantic ranked all at #1. Hybrid’s only soft miss was one note at rank 4.

---

## Build Order

Unchanged three-stage order — see Build Plan for phase detail.

### Stage 1 — Librarian Baseline
Vault I/O + structured retrieval + CLI harness, no LLM.

### Stage 2 — Librarian Eval
Full retrieval stack + auto-generated eval harness + intent classification + confidence heuristics.

### Stage 3 — PA
Hermes + Telegram + Librarian wired as MCP server.

---

## Planned Experiment: Token A/B Benchmark

Build ~10–15 representative test requests spanning all retrieval paths + create/update/delete. Run each through both:
- **Arm A — current design:** PA → Librarian (full custom logic: schema validation, intent classification, confidence heuristics, exact/semantic/hybrid retrieval, groundedness check) → vault
- **Arm B — passthrough Librarian:** PA → Librarian (thin forwarding layer only — no schema validation, no classification, no confidence scoring; Librarian remains the sole vault writer for concurrency safety, but simply hands the raw request to Obsidian's MCP and lets the LLM freely read/write/reason over vault content) → vault

Both arms keep Librarian as the single vault-writing gatekeeper (avoids concurrent-write races if multiple agents exist later) — the isolated variable is purely whether structured retrieval/schema/confidence logic adds value over freeform LLM reasoning, not whether a gatekeeper is needed at all.

Log tokens per request (classification + generation + tool overhead), compare totals and *where* cost concentrates, plus correctness/accuracy on structured queries (counts, exact lookups) where Arm B has no deterministic guardrail. Hypothesis: direct-Obsidian-MCP loses most on structured/keyword queries (reasoning over raw vault content in-context instead of hitting SQLite); gap narrows on semantic queries where both need an LLM call regardless. Strong portfolio material — quantifies the two-agent split's actual payoff rather than asserting it.

---

## Known Edge Cases / Accuracy Limitations (documented honestly)

1. **Aggregation undercount** — mitigated by the dual-check + transparent discrepancy flag (see Retrieval Taxonomy above), but not fully eliminated; anything never tagged consistently can still be missed.
2. **Implicit multi-hop relations** — connections never wikilinked (or LLM-suggested) won't surface. Accepted tradeoff of rejecting GraphRAG.
3. **Fuzzy time + fuzzy content combined** — partially mitigated by temporal extraction in the hybrid path; imperfect natural-language time expressions may still misparse.
4. **Cross-note chronological synthesis** ("how has my thinking on X changed this year") — top-k similarity retrieval isn't built for broad chronological synthesis; out of scope for v1.
5. **Ambiguous delete/update targets** where the correct target isn't the most recent match — recency heuristic doesn't help here; relies on confidence-gated clarification to catch it.

---

## Known Tradeoffs

- **Schema evolves, not pre-locked** — by design.
- **Token cost per vault-touching request is non-trivial** but negligible in dollars at ~10 requests/day — post-launch audit item, not a pre-build blocker.
- **No conflict resolution beyond git's default merge behavior** — acceptable at single-user scale.
- **Kanban/multi-agent dispatch deliberately dropped.**
- **Full GraphRAG (and LightRAG) deliberately out of scope** — evaluated with real cost figures, not just assumed.
- **Accuracy-vs-cost is an explicit, named bet** — this architecture trades some of the "always correct, never interrupts" feel of tools like Cursor+Notion MCP (narrow pre-built tools, large context, strong model, human-in-the-loop, no metered cost) for token efficiency and autonomy. That tradeoff should be stated explicitly in any portfolio writeup, not treated as an unexplained accuracy gap.

---

## Open Items — Flagged for Revisit

1. **f-string templates for `exact_lookup` responses** — dissatisfaction remains; alternative rendering approach to be explored.
2. **Intent classification taxonomy still feels rigid** at edge cases — confidence-gated clarification helps but doesn't fully resolve; optional `secondary` intent field floated as a future softening, not yet designed.
3. **Multi-intent handling** (single message, multiple intents) — deferred past v1, approach sketched above.
4. **Video ingestion** — deferred, stretch-goal tier, no auto-understanding planned for v1.
5. **Retrieval module collapse (5 conceptual → 3 implementation)** — decided, not yet implemented; verify no behavioral regression when merging keyword+structured code paths.
6. ~~Reaction logging scope~~ — resolved: `is_reaction` logs on successful write; broad casual affect still dropped.
7. **Preference synthesis cadence** — weekly vs monthly cron, and how much accumulated data is "enough" to run it meaningfully — not yet determined, revisit once Stage 1/2 usage data exists.
8. **Honcho vs Hermes built-in memory** — defaulting to built-in for v1; revisit only if conversational memory proves insufficient in practice.
9. **LangGraph adoption** — rejected for v1; watchlist of concrete trigger conditions in Agent Frameworks & Orchestration section. Not a timeline-based revisit — only re-evaluate if one of the listed conditions is actually hit.

---

## Prior Art Cited

- **agent-second-brain** — typed graph memory (`autograph`), same schema.json-as-source-of-truth philosophy, independently-converged Ebbinghaus decay mechanism (same formula shape, same 5 tiers), flat-subscription cost model (considered, not adopted — usage-cap risk noted).
- **obsidian-second-brain** — auto-generated eval harness pattern (adopted), measured hybrid search recall gains (80%→91% exact-term, 17%→46% paraphrased on a 1,000-note vault) as real evidence hybrid retrieval is worth it at scale.
- **open-second-brain** — Hermes-native, deterministic update logic, MCP-based; closest stack match, worth reviewing before finalizing Stage 1.
- **Smart Second Brain** (Obsidian plugin) — vector-only RAG baseline for comparison.

---

## Key Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Agent split | PA (Hermes) + Librarian (custom, Python, from scratch) | PA stays thin; Librarian is the portfolio-relevant engineering |
| Communication | Direct MCP tool call, synchronous | Lower latency than Kanban; simpler ops for single caller |
| Librarian transport | MCP over stdio, co-located on VPS | No network exposure needed |
| Retrieval routing | Combined intent + mode classification, one LLM call; no rule pre-filter (removed — conflicted with typed create guards) | All requests classified; empty-create guards downstream |
| Retrieval implementation | 5 conceptual paths, collapsed to 3 code modules | Keyword+structured share one non-LLM lookup mechanism; fewer things to build/maintain |
| Confidence scoring | Heuristic (vector margin + coreference count), not LLM self-report | LLM self-reported confidence clusters near 0.9 regardless of correctness |
| Delete | New 4th intent, always confirm, soft-delete to `.trash/` | Destructive action needs more friction than create/update/query |
| Groundedness check | Added, semantic/hybrid paths only | Real hallucination risk on generation paths; no cost on deterministic paths |
| Confirm-before-write (LLM re-check) | Rejected | Redundant with upstream confidence-gated clarification |
| Reranking | Rejected | Gains matter at 20+ candidates; not the case at personal-vault scale |
| GraphRAG / LightRAG | Rejected, with real cost figures evaluated | Overhead doesn't pay off under ~100K token corpus; wikilinks + LLM-suggested links cover the practical need |
| Wikilink generation | LLM-suggested at ingestion (1-2 links, same call) | Manual linking assumption broken — user doesn't use Obsidian directly |
| Aggregation | Sub-flag of structured path + dual-check for undercounts | Prevents silently-wrong counts from schema-on-read's fallback bucket |
| Eval harness | Auto-generated from vault notes (question-per-note, recall@k) | Self-updating, stronger than fixed hand-written case set |
| RAG scope | Semantic and hybrid paths only | Writes and structured queries have exact answers |
| Schema approach | Schema-on-read, user-approved formalization | Avoids pre-guessing schema; avoids autonomous restructuring |
| Target resolution | Context → recency heuristic → clarification | Handles coreference without guessing on real ambiguity |
| Vault sync | Bidirectional | One-way design would silently diverge |
| Build order | Librarian Baseline → Librarian Eval → PA | Fast submittable checkpoint; proves correctness before PA depends on it |
| Image input | Vision caption at PA, then treated as text | Same pattern as voice; keeps Librarian text-only and simple |
| Video input | Deferred, attachment + caption only | No cheap "understand this video" primitive at budget |
| Correction logging | `corrections` table; log on successful update when `is_reaction` or approved conflict | Signal captured at moment of fix; vault always updated for factual corrections |
| Reaction logging (broad affect) | Dropped | "oh nice" affirmations too noisy/ambiguous |
| PA→Librarian correction signal | Classifier `is_reaction` + normal mutate path | No log-only shortcut; no turn-adjacency inference |
| Conversational memory provider | Hermes built-in memory (default), Honcho deferred | Avoids extra external dependency before proven necessary; config-only upgrade later if needed |
| Orchestration approach | Custom Python (both PA and Librarian); no LangChain, LangGraph, or skill-based agent pattern | No multi-provider need, flow is small/fixed-shape, differentiated logic (confidence heuristics, schema validation) has no framework primitive to use; see Agent Frameworks section |
