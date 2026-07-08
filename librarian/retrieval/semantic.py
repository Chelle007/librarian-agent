"""Semantic retrieval path — vector search over note chunks.

This is the retrieval half of the `semantic` module: embed the query (as a
RETRIEVAL_QUERY), KNN against the vector store, return top-k notes. RAG
generation (turning hits into a grounded answer) is a separate later step that
adds the Gemini generation + groundedness call on top of these hits — kept out
of here so retrieval is testable offline with the HashingEmbedder.
"""

from __future__ import annotations

from librarian.llm.embeddings import Embedder
from librarian.store.vector_store import NoteHit, VectorStore


class SemanticRetriever:
    def __init__(self, vector_store: VectorStore, embedder: Embedder):
        if vector_store.dim != embedder.dim:
            raise ValueError(
                f"vector store dim {vector_store.dim} != embedder dim {embedder.dim}"
            )
        self.vector_store = vector_store
        self.embedder = embedder

    def search(self, query: str, k: int = 5) -> list[NoteHit]:
        """Return the top-k most semantically similar notes to `query`."""
        query = (query or "").strip()
        if not query:
            return []
        query_vec = self.embedder.embed_query(query)
        return self.vector_store.search(query_vec, k=k)
