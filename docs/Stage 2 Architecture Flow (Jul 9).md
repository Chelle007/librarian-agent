# Stage 2 Architecture Flow

How `LibrarianAgent.handle()` routes free text from entry (CLI / MCP / PA) through the LLM layer into the deterministic Stage 1 `Librarian` core.

## Full flow

```mermaid
flowchart TB
    subgraph Entry["Entry"]
        CLI["CLI: librarian handle"]
        MCP["MCP: librarian_handle"]
        PA["PA / chat client<br/>(passes context on follow-ups)"]
    end

    subgraph Stage2["Stage 2 — LibrarianAgent (LLM router)"]
        HANDLE["handle(raw_request, context)"]

        subgraph Classify["1. Classify"]
            LLM_CLASS["Classifier LLM<br/>intent + mode + fields<br/>actionable"]
            HANDLE --> LLM_CLASS
            LLM_CLASS --> ACTIONABLE
            ACTIONABLE{"actionable?"}
            ACTIONABLE -->|no| CLARIFY_OUT["needs_clarification"]
        end

        ACTIONABLE -->|yes| ROUTE{"Route by intent"}

        ROUTE -->|is_reaction +<br/>meta correction| REACTION["log_correction<br/>→ done"]
        ROUTE -->|create / update| MUTATE["_mutate()"]
        ROUTE -->|query| QUERY["_query()"]
        ROUTE -->|delete| DELETE["_delete()"]

        subgraph Mutate["2. Create / Update path"]
            WRITE_RES["resolve_write_target()"]
            WRITE_RES --> WR1["1. context path"]
            WR1 --> WR2["2. explicit target_ref path"]
            WR2 --> WR3["3. schema identity"]
            WR3 --> WR4["4. vague reference → semantic search"]

            WR4 --> WR_ACTION{"action?"}
            WR_ACTION -->|clarify| TARGET_CONFIRM["→ needs_clarification"]
            WR_ACTION -->|create / update| MENTION_GATE

            MENTION_GATE{"_gate_mentions()"}
            MENTION_GATE -->|mentions found| PENDING_M["store pending + pending_id"]
            MENTION_GATE -->|none| APPLY

            PENDING_M --> CLARIFY_OUT
            APPLY --> CREATE or UPDATE
            CREATE --> STAGE1_CREATE
            UPDATE --> CONFLICT_LLM["check_update_conflict"]
            CONFLICT_LLM -->|conflict| PENDING_C["store pending + pending_id"]
            PENDING_C --> CLARIFY_OUT
            CONFLICT_LLM -->|ok| STAGE1_UPDATE
        end

        CONFIRM["handle_confirm(pending_id, approved)"]
        CONFIRM -->|approved| EXEC["execute stored snapshot"]
        CONFIRM -->|rejected / expired| CLARIFY_OUT

        MUTATE --> Mutate

        subgraph Query["3. Query path"]
            QUERY --> QMODE{"mode?"}
            QMODE -->|exact_lookup| EXACT["ExactLookup<br/>type / tag / keyword / date / aggregate"]
            QMODE -->|semantic| SEM["SemanticRetriever<br/>vector search k=5"]
            QMODE -->|hybrid| HYB["HybridRetriever<br/>metadata filter + vector search"]
            SEM --> RAG["generate_answer LLM"]
            HYB --> RAG
            RAG --> GROUND["check_groundedness LLM"]
            EXACT --> QUERY_OUT["done · queried"]
            GROUND --> QUERY_OUT
            SEM -->|vectors off| ERR_OUT["error"]
            HYB -->|vectors off| ERR_OUT
        end

        subgraph DeletePath["4. Delete path"]
            DELETE --> DEL_RES["resolve_target()"]
            DEL_RES -->|ambiguous| DEL_CLARIFY["→ needs_clarification"]
            DEL_RES -->|resolved| PENDING_D["store pending + pending_id (1h TTL)"]
            PENDING_D --> CLARIFY_OUT
        end
    end

    subgraph Stage1["Stage 1 — Librarian (deterministic)"]
        STAGE1_CREATE["lib.create()<br/>schema validate → VaultIO write → meta index"]
        STAGE1_UPDATE["lib.update()<br/>merge frontmatter + body → reindex"]
        STAGE1_DELETE["lib.delete()<br/>soft-delete → .trash"]
    end

    subgraph Storage["Storage layer"]
        VAULT["VaultIO<br/>Obsidian .md files"]
        META["MetadataStore<br/>SQLite index"]
        VEC["VectorStore<br/>chunk embeddings"]
        SCHEMA["schema.json<br/>types + required fields"]
    end

    CLI --> HANDLE
    MCP --> HANDLE
    PA --> HANDLE

    STAGE1_CREATE --> VAULT
    STAGE1_CREATE --> META
    STAGE1_UPDATE --> VAULT
    STAGE1_UPDATE --> META
    STAGE1_DELETE --> VAULT
    STAGE1_DELETE --> META

    EXACT --> META
    EXACT --> VAULT
    SEM --> VEC
    HYB --> VEC
    HYB --> META

    STAGE1_CREATE --> DONE["done<br/>note_id + action"]
    STAGE1_UPDATE --> DONE
    STAGE1_DELETE --> DONE
    QUERY_OUT --> DONE
    REACTION --> DONE
    ERR_OUT --> DONE

    classDef llm fill:#e8f4fd,stroke:#4a90d9
    classDef gate fill:#fff3cd,stroke:#d4a017
    classDef out fill:#d4edda,stroke:#28a745
    classDef core fill:#f0f0f0,stroke:#666

    class LLM_CLASS,RECOVER_LLM,RECOVER_UPD,CONFLICT_LLM,RAG,GROUND llm
    class ACTIONABLE,RECOVER,WR_ACTION,MENTION_GATE,EMPTY_GUARD,PENDING_UPD,CONFLICT,DEL_CONFIRM gate
    class CLARIFY_OUT,TARGET_CONFIRM,MENTION_CONFIRM,CONFLICT_CONFIRM,DEL_CLARIFY,DEL_ASK,DONE,ERR_OUT out
    class STAGE1_CREATE,STAGE1_UPDATE,STAGE1_DELETE,VAULT,META,VEC,SCHEMA core
```

## Layer summary

| Layer | Role |
|---|---|
| **Entry** | `CLI` / `MCP` / PA chat — PA must relay `context` on follow-up confirms |
| **Classifier** | One LLM call → intent, fields, `actionable` |
| **Pending confirms** | SQLite snapshot + `pending_id`; PA/CLI calls `handle_confirm()` |
| **Gates** | Mention confirm, conflict check, delete confirm → all return `pending_id` |
| **Stage 1** | `Librarian` — schema-validated writes, no LLM |
| **Storage** | Vault files + SQLite metadata + optional vectors |

## Confirm loop

```mermaid
sequenceDiagram
    participant U as User / PA
    participant A as LibrarianAgent
    participant DB as pending_confirmations
    participant V as Vault

    U->>A: "my bestie is Angeline"
    A->>V: find_mentions("Angeline")
    A->>DB: store snapshot
    A-->>U: needs_clarification + pending_id

    U->>A: handle_confirm(pending_id, approved=true)
    A->>DB: load + settle
    A->>V: create contact + merge links
    A-->>U: done · created 👤 contacts/angeline.md
```

TTL: 24h default, 1h for delete. Expired IDs return `needs_clarification`.

## Key modules

| Module | Responsibility |
|---|---|
| `librarian/agent.py` | Orchestrator — `handle()` and all route methods |
| `librarian/classifier.py` | Intent classification |
| `librarian/write_resolution.py` | Create vs update vs clarify (schema identity first) |
| `librarian/target_resolution.py` | Resolve vague refs for update/delete (semantic + recency) |
| `librarian/mention_search.py` | Word-boundary vault scan for identity labels |
| `librarian/link_resolution.py` | Wikilink merge + mention confirm formatting |
| `librarian/note_preview.py` | One-line note previews for confirm prompts |
| `librarian/pending_confirm.py` | TTL constants + `classification_from_pending()` |
| `librarian/store/metadata_store.py` | `pending_confirmations` table CRUD |
| `librarian/llm/update_check.py` | LLM conflict detection before updates |
| `librarian/pipeline.py` | Stage 1 `Librarian` — deterministic vault I/O |

## Return contract

Every `handle()` call returns a `HandleResult`:

| Field | Values |
|---|---|
| `status` | `done` · `needs_clarification` · `error` |
| `message` | Human-readable response (relay back to user on clarify) |
| `note_id` | Vault path when applicable |
| `action` | `created` · `updated` · `deleted` · `queried` · `None` |
| `pending_id` | Set when `status=needs_clarification` and user must approve (for PA buttons / `librarian confirm`) |
