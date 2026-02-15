"""
External Docs RAG — Retrieval-Augmented Generation for documentation.

Provides a framework for ingesting and querying external documentation:
    - API docs, library references, framework guides
    - Stored in ChromaDB for semantic retrieval
    - Falls back to URL-based lookup when ChromaDB is unavailable
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocChunk:
    """A chunk of documentation text."""
    source: str          # URL or file path
    title: str
    content: str
    chunk_id: str = ""
    
    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = hashlib.sha256(
                f"{self.source}:{self.content[:100]}".encode()
            ).hexdigest()[:12]


@dataclass
class DocSearchResult:
    """Result from a documentation search."""
    chunks: list[DocChunk]
    query: str
    source: str = "local"  # local, chromadb, web


class DocsStore:
    """
    Manages a local documentation store for RAG.
    
    Supports:
        - Ingesting documentation from text
        - Querying by keyword or semantic similarity
        - ChromaDB integration (optional)
    """

    def __init__(self, collection_name: str = "external_docs"):
        self._chunks: dict[str, DocChunk] = {}
        self._collection_name = collection_name
        self._chromadb_available = False
        self._collection = None
        self._try_chromadb()

    def _try_chromadb(self):
        """Try to initialize ChromaDB for semantic search."""
        try:
            import chromadb
            client = chromadb.Client()
            self._collection = client.get_or_create_collection(
                name=self._collection_name
            )
            self._chromadb_available = True
            logger.info("DocsStore: ChromaDB initialized")
        except ImportError:
            logger.info("DocsStore: chromadb not installed, using keyword fallback")

    def ingest(
        self,
        source: str,
        content: str,
        title: str = "",
        chunk_size: int = 500,
    ) -> int:
        """
        Ingest documentation content into the store.
        
        Splits content into chunks and indexes them.
        Returns number of chunks created.
        """
        paragraphs = content.split("\n\n")
        chunks_created = 0

        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                chunk = DocChunk(
                    source=source,
                    title=title,
                    content=current_chunk.strip(),
                )
                self._store_chunk(chunk)
                chunks_created += 1
                current_chunk = para
            else:
                current_chunk += "\n\n" + para

        # Store remaining content
        if current_chunk.strip():
            chunk = DocChunk(
                source=source,
                title=title,
                content=current_chunk.strip(),
            )
            self._store_chunk(chunk)
            chunks_created += 1

        logger.info(f"Ingested {chunks_created} chunks from {source}")
        return chunks_created

    def query(self, question: str, n_results: int = 5) -> DocSearchResult:
        """
        Query the documentation store.
        
        Uses ChromaDB semantic search if available, keyword fallback otherwise.
        """
        if self._chromadb_available and self._collection:
            return self._query_chromadb(question, n_results)
        return self._query_keyword(question, n_results)

    def _store_chunk(self, chunk: DocChunk):
        """Store a chunk in both local cache and ChromaDB."""
        self._chunks[chunk.chunk_id] = chunk

        if self._chromadb_available and self._collection:
            self._collection.upsert(
                documents=[chunk.content],
                ids=[chunk.chunk_id],
                metadatas=[{
                    "source": chunk.source,
                    "title": chunk.title,
                }],
            )

    def _query_chromadb(self, question: str, n_results: int) -> DocSearchResult:
        """Query using ChromaDB semantic search."""
        try:
            results = self._collection.query(
                query_texts=[question],
                n_results=n_results,
            )

            chunks = []
            if results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    chunks.append(DocChunk(
                        source=meta.get("source", ""),
                        title=meta.get("title", ""),
                        content=doc,
                    ))

            return DocSearchResult(
                chunks=chunks,
                query=question,
                source="chromadb",
            )
        except Exception as e:
            logger.warning(f"ChromaDB query failed: {e}")
            return self._query_keyword(question, n_results)

    def _query_keyword(self, question: str, n_results: int) -> DocSearchResult:
        """Fallback keyword search."""
        words = question.lower().split()
        scored_chunks = []

        for chunk in self._chunks.values():
            content_lower = chunk.content.lower()
            score = sum(1 for w in words if w in content_lower)
            if score > 0:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        return DocSearchResult(
            chunks=[c for _, c in scored_chunks[:n_results]],
            query=question,
            source="keyword",
        )

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)
