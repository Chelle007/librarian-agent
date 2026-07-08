# Embedding Model & Chunking — Locked Decision

> Last updated: July 2026
> Status: Decided (Stage 2 gate) — lock before building the vector store
> North star reminder: code sustainability > accuracy = token efficiency = latency

This is the one Stage 2 decision that's expensive to change: the embedding model, its output dimensions, and the chunking granularity all get baked into every stored vector. Changing the **model or dimensions later requires re-embedding the whole vault**; changing **chunking** requires re-embedding too. This doc locks all three, and — importantly — designs the storage so a *chunking* change never needs a schema migration, only a re-embed.

---

## TL;DR — the locked choices

| Decision | Choice | One-line why |
|---|---|---|
| Embedding model | **`gemini-embedding-2`** (multimodal) | Future image embeddings share ONE vector space with text (cross-modal retrieval); 8192-token input; same vendor as the LLM |
| Output dimensions | **768** (via Matryoshka truncation) | ~0.26%-class quality loss vs 3072, 25% the storage, faster brute-force KNN |
| Normalization | **L2-normalize every vector** centrally | `-2` auto-normalizes truncated dims, `-001` does not — normalizing unconditionally is a harmless no-op and keeps the two models interchangeable |
| Asymmetric retrieval | **Text prefixes** (`task: search result \| query: …` / `title: … \| text: …`) | `-2` has no `task_type` field; the task is expressed as an input prefix instead (capability intact, just a different shape) |
| Vector engine | **`sqlite-vec`** (`vec0`, float32, brute-force KNN) | Personal-vault scale; no separate DB; matches the SQLite metadata store already built |
| Chunk granularity | **Chunk-native schema; whole-note = 1 chunk** | Most personal notes are short → 1 chunk; a chunking change later = re-embed only, no migration |
| Chunking policy v1 | Structured types = whole-note; freeform = whole-note under ~1000 tokens, else structure-aware split | Only long freeform notes pay the chunking complexity (and `-2`'s 8192-token window means splitting is rarely needed) |

Everything below is the reasoning. If you just want to build, the table is the contract.

---

## 1. Embedding model

### The real choice: Gemini API vs a local sentence-transformer

| | Gemini API (`gemini-embedding-2`) | Local (e.g. `bge-small`/`all-MiniLM`) |
|---|---|---|
| Quality | Strong (Gemini family; text-retrieval MTEB not yet published for `-2`) | Good, a notch below |
| Cost | $0.20/M text tokens → **effectively $0 at ~10 req/day** | Free |
| Infra on CX22 (2 vCPU / 4 GB) | **None** — just an HTTP call | torch/onnx + model in RAM, competes with Hermes for 4 GB |
| Dependencies (sustainability) | `google-genai` SDK, a few lines | torch + model management + download pipeline |
| Offline | Needs network | Works offline |
| Privacy | Content already goes to Gemini for classification/RAG | Content stays local |
| Multimodal | ✅ (`-2` is natively multimodal) | Would need a *separate* CLIP-style model + its own space |

### Decision: **Gemini API, `gemini-embedding-2`**

First, API over local — rationale ranked by the north star:

1. **Sustainability (top priority).** An HTTP call vs standing up torch + a model file + download/version management on a 4 GB box that also runs Hermes. Fewer moving parts, no RAM contention. Deciding factor.
2. **No new privacy exposure.** Vault content *already* transits Gemini for classification and RAG — the "keep embeddings local for privacy" argument doesn't apply; that boundary was crossed by design.
3. **Offline isn't a real risk.** The VPS is always-on, and if Gemini is unreachable the whole assistant is down regardless — embeddings add no *new* single point of failure.
4. **Cost is a non-issue.** At ~10 req/day, embed tokens round to a fraction of a cent/day. Batch mode ($0.10/M) exists for any bulk re-embed.

Then `-2` over `-001` — the decisive reason is **one vector space for a multimodal future**:

- An embedding model *defines* a vector space; vectors from different models aren't comparable. Native image embedding is a real future goal, and cross-modal retrieval (query in text → match an image) only works if text and images are embedded by the **same** multimodal model into the **same** space. Starting on `-2` gets that for free; starting on `-001` would force a full re-embed of everything the day images are added.
- Secondary wins: **8192-token input** (4× `-001`) means long notes rarely need chunking; **auto-normalization** of truncated dims removes a footgun; same Matryoshka dims and `google-genai` SDK.
- Cost delta ($0.20 vs $0.15/M) is immaterial at this volume.

**Dimensions: 768.** Matryoshka truncation of the 3072-d default — near-zero quality loss for 25% the storage and faster brute-force KNN. 1536/3072 buy essentially nothing at personal-vault scale.

### The one thing that's different from `-001` (and easy to get wrong)

- **Asymmetric retrieval is via text prefixes, not a `task_type` field.** `-2` has no `task_type` param. Instead, prefix the input: queries as `task: search result | query: {text}`, documents as `title: {title} | text: {content}`. This is a documented, stable pattern — asymmetric retrieval (the free accuracy win) is fully intact, just expressed differently. The embed helper handles this per-model so no call site deals with it.
- **Normalization is centralized anyway.** `-2` auto-normalizes truncated dims (`-001` doesn't). The embed helper L2-normalizes unconditionally — a harmless no-op on `-2`, required on `-001` — so the two stay drop-in interchangeable.

### Input limit

`-2` allows **8192 tokens** per input (vs 2048 on `-001`). A single note's embed text almost never approaches this, so chunking is driven by *retrieval quality* on long notes, not a hard API cap (see §3).

> Fallback / A-B option: **`gemini-embedding-001`** (text-only, 2048-token cap, `task_type` enum, $0.15/M, published MTEB ~68.3). The embed helper supports it via the same class, so the eval harness can A/B `-2` vs `-001` on text-retrieval quality. Switch is a cheap re-embed at this scale. Reasons to consider it: if `-2`'s text-retrieval quality underperforms `-001` in the eval, and multimodal is still far off.

---

## 2. Vector engine — `sqlite-vec`

- Confirmed active again (v0.1.7, Mar 2026) after a hiatus; still **pre-v1, so pin the version** and expect occasional breaking changes.
- `vec0` virtual tables, float32 vectors, cosine/L2 distance, SIMD-accelerated **brute-force KNN** — which is exactly right at personal-vault scale (hundreds–low thousands of chunks). The alpha ANN indexes (DiskANN etc.) are for far larger corpora; ignore for v1.
- Fits the existing stack perfectly: the metadata store is already SQLite, so this is the *same file/engine* rather than a new service — a sustainability win.
- Python: `pip install sqlite-vec`, `sqlite_vec.load(conn)`, store via `serialize_float32(...)` (or a `np.float32` array). Pin it in `requirements.txt`.

---

## 3. Chunking strategy

### The key move: make the schema chunk-native, so chunking is re-embeddable without migration

Store vectors at **chunk granularity from day one**, where a whole note is simply a note that produced **one** chunk. That way, changing the chunking policy later (e.g. splitting long notes more aggressively) is *only* a re-embed of affected notes — never a schema change.

```
chunks(
  chunk_id     TEXT PRIMARY KEY,   -- e.g. "notes/foo.md#0"
  note_path    TEXT NOT NULL,      -- FK-ish to metadata store's notes.path
  chunk_index  INTEGER NOT NULL,   -- 0 for whole-note
  text         TEXT NOT NULL,      -- the exact text that was embedded
  embedding    float[768]          -- in the sqlite-vec vec0 table
)
```

Retrieval scores at **note granularity**: multiple chunk hits from the same note collapse to that note, taking the best (min-distance) chunk as the note's score. The eval harness's recall@k is measured on notes, not chunks.

### Policy v1 (deliberately minimal)

- **Structured / short types** (`contact`, `task`, `habit`, `brief`): **always whole-note, 1 chunk.** They're small and atomic — chunking adds rows and complexity for zero gain.
- **Freeform types** (`note`, and future `journal`/`growth`):
  - Under ~1000 tokens → **whole-note, 1 chunk** (far inside the model's 8192-token limit).
  - Over ~1000 tokens → **structure-aware split** on markdown headings/paragraph boundaries, targeting ~500–800 tokens per chunk with ~10–15% overlap, each chunk conservatively capped (~1800 tokens) — this is a *retrieval-quality* choice now, not an API cap, since `-2` allows 8192.

Rationale: the vast majority of personal notes are short and become a single chunk, so the common path stays dead-simple. Only genuinely long notes (long journal entries, dumped articles) pay the chunking cost — and for those, granularity materially helps retrieval (a single averaged embedding of a 2000-word note is a blurry query target).

### What to keep aligned

- Chunk text stored verbatim (`chunks.text`) so RAG generation grounds on the exact embedded span, and so a re-embed doesn't require re-reading/re-splitting from the note if the policy is unchanged.
- The chunker lives in `ingestion/chunker.py` and is the single place the policy is expressed, so tuning it is one file.

---

## 4. Deferred / not in v1 (noted so they're not silently forgotten)

- **int8 / binary quantization** (`sqlite-vec` supports it) — a storage/speed optimization only worth it at much larger scale; float32 @ 768 is tiny here.
- **ANN indexes** (DiskANN/ivf, alpha) — brute-force KNN is faster to reason about and plenty fast at this scale.
- **`gemini-embedding-2`** multimodal / 8192-token input — forward option (see §1 note).
- **Re-chunking existing long notes on policy change** — cheap re-embed thanks to the chunk-native schema; do it if/when the eval harness shows long-note recall lagging.

---

## 5. Consequences for the build plan

- Stage 2 "lock embedding model + chunking" gate is now **closed** — proceed to build `store/vector_store.py` against: `gemini-embedding-2`, 768-d, L2-normalized, prefix-based asymmetric retrieval (`task: search result | query: …` / `title: … | text: …`), `sqlite-vec` `vec0`, chunk-native schema above. (`-001` retained in the embed helper as an A/B fallback.)
- `ingestion/chunker.py` implements Policy v1 (§3).
- `llm/gemini_client.py` (or a small `embeddings.py`) owns the embed call + the mandatory renormalization, so normalization and task-type can never be forgotten at a call site.
- Pin `sqlite-vec` and `google-genai` versions in `requirements.txt` (sqlite-vec is pre-v1).
