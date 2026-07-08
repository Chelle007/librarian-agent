"""`hybrid` retrieval — metadata pre-filter + vector search within the survivors.

The hybrid path combines the two other retrieval styles: a structured filter
(type / tag / date range) narrows the candidate set via the metadata index, then
a semantic vector search ranks *only within that set*. This is what handles
"that article about sourdough I saved last month" — the date range is a hard
metadata filter, the topic is the vector query.

Temporal fuzziness ("a while back", "last month") is turned into a concrete
`created_after` / `created_before` range **upstream**, by the classification
call — this module takes an already-resolved range, so it stays LLM-free and
testable offline. RAG generation + groundedness sit on top of these hits as a
separate step (kept out of here so retrieval is unit-testable with the offline
HashingEmbedder), mirroring `retrieval/semantic.py`.
"""

from __future__ import annotations

from librarian.llm.embeddings import Embedder
from librarian.store.metadata_store import MetadataStore
from librarian.store.vector_store import NoteHit, VectorStore


class HybridRetriever:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        meta: MetadataStore,
    ):
        if vector_store.dim != embedder.dim:
            raise ValueError(
                f"vector store dim {vector_store.dim} != embedder dim {embedder.dim}"
            )
        self.vector_store = vector_store
        self.embedder = embedder
        self.meta = meta

    def search(
        self,
        query: str,
        *,
        type: str | None = None,
        tag: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        k: int = 5,
    ) -> list[NoteHit]:
        """Metadata-filter to candidates, then vector-rank within them (top-k notes).

        Returns [] if the query is empty or the metadata filter matches nothing —
        an empty candidate set means there is nothing to rank, not "rank globally".
        """
        query = (query or "").strip()
        if not query:
            return []

        candidates = self.meta.query(
            type=type,
            tag=tag,
            created_after=created_after,
            created_before=created_before,
            limit=None,
        )
        allowed = {row["path"] for row in candidates}
        if not allowed:
            return []

        query_vec = self.embedder.embed_query(query)
        return self.vector_store.search(query_vec, k=k, note_paths=allowed)
