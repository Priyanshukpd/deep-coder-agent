"""
Code Search — ripgrep wrapper + semantic search interface.

Tier 1: ripgrep (fast, exact/regex text search)
Tier 2: Semantic search (ChromaDB embeddings, when available)

The agent should try Tier 1 first, falling back to Tier 2
for concept-level queries.
"""

from __future__ import annotations

import subprocess
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""
    file_path: str
    line_number: int
    line_content: str
    match_type: str = "exact"  # exact, regex, semantic
    score: float = 1.0         # Relevance score (1.0 for exact match)


@dataclass
class SearchResponse:
    """Aggregate search response."""
    query: str
    results: list[SearchResult] = field(default_factory=list)
    search_tier: str = "ripgrep"
    total_matches: int = 0
    truncated: bool = False

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0


class RipgrepSearch:
    """
    Tier 1: Fast text search using ripgrep.
    
    Wraps the `rg` command for exact and regex pattern matching.
    """

    def __init__(self, root_dir: str = "."):
        self._root = root_dir

    def search(
        self,
        query: str,
        file_pattern: str = "",
        max_results: int = 50,
        case_insensitive: bool = True,
        regex: bool = False,
    ) -> SearchResponse:
        """
        Search for a pattern using ripgrep.
        
        Args:
            query: Search pattern
            file_pattern: Glob pattern to filter files (e.g., "*.py")
            max_results: Maximum results to return
            case_insensitive: Case-insensitive search
            regex: Treat query as regex
        """
        cmd = ["rg", "--json", "-m", str(max_results)]

        if case_insensitive:
            cmd.append("-i")
        if not regex:
            cmd.append("-F")  # Fixed string (literal)
        if file_pattern:
            cmd.extend(["-g", file_pattern])

        cmd.append(query)
        cmd.append(self._root)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )

            results = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "match":
                        data = obj["data"]
                        results.append(SearchResult(
                            file_path=data.get("path", {}).get("text", ""),
                            line_number=data.get("line_number", 0),
                            line_content=data.get("lines", {}).get("text", "").strip(),
                            match_type="regex" if regex else "exact",
                        ))
                except json.JSONDecodeError:
                    pass

            return SearchResponse(
                query=query,
                results=results[:max_results],
                search_tier="ripgrep",
                total_matches=len(results),
                truncated=len(results) > max_results,
            )

        except FileNotFoundError:
            logger.warning("ripgrep (rg) not found — falling back to grep")
            return self._fallback_grep(query, file_pattern, max_results)
        except subprocess.TimeoutExpired:
            return SearchResponse(query=query, search_tier="ripgrep (timeout)")

    def _fallback_grep(
        self, query: str, file_pattern: str, max_results: int
    ) -> SearchResponse:
        """Fallback to grep if ripgrep is not installed."""
        cmd = ["grep", "-rn", "-m", str(max_results)]
        if file_pattern:
            cmd.extend(["--include", file_pattern])
        cmd.append(query)
        cmd.append(self._root)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            results = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    results.append(SearchResult(
                        file_path=parts[0],
                        line_number=int(parts[1]) if parts[1].isdigit() else 0,
                        line_content=parts[2].strip(),
                        match_type="exact",
                    ))

            return SearchResponse(
                query=query,
                results=results,
                search_tier="grep",
                total_matches=len(results),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return SearchResponse(query=query, search_tier="unavailable")


class SemanticSearch:
    """
    Tier 2: Semantic search using ChromaDB embeddings.
    
    Requires chromadb to be installed. Falls back gracefully if not available.
    
    Usage:
        semantic = SemanticSearch()
        if semantic.is_available:
            semantic.index_file("agent/state.py", content)
            results = semantic.search("state machine transitions")
    """

    def __init__(self, collection_name: str = "codebase"):
        self._collection_name = collection_name
        self._client = None
        self._collection = None
        self._available = False
        self._try_init()

    def _try_init(self):
        """Try to initialize ChromaDB. Graceful failure if not installed."""
        try:
            import chromadb
            self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name
            )
            self._available = True
            logger.info("Semantic search: ChromaDB initialized")
        except ImportError:
            logger.info("Semantic search: chromadb not installed, disabled")
        except Exception as e:
            logger.warning(f"Semantic search init failed: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    def index_file(self, file_path: str, content: str, chunk_size: int = 500):
        """Index a file's content into ChromaDB."""
        if not self._available:
            return

        # Split content into chunks
        lines = content.split("\n")
        chunks = []
        for i in range(0, len(lines), chunk_size // 5):
            chunk = "\n".join(lines[i:i + (chunk_size // 5)])
            if chunk.strip():
                chunks.append(chunk)

        # Add to collection
        for i, chunk in enumerate(chunks):
            doc_id = f"{file_path}:{i}"
            self._collection.upsert(
                documents=[chunk],
                ids=[doc_id],
                metadatas=[{"file": file_path, "chunk": i}],
            )

    def search(self, query: str, n_results: int = 10) -> SearchResponse:
        """Search using semantic similarity."""
        if not self._available:
            return SearchResponse(
                query=query,
                search_tier="semantic (unavailable)",
            )

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
            )

            search_results = []
            if results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    distance = results["distances"][0][i] if results["distances"] else 0
                    search_results.append(SearchResult(
                        file_path=meta.get("file", "unknown"),
                        line_number=meta.get("chunk", 0) * 100,
                        line_content=doc[:200],
                        match_type="semantic",
                        score=1.0 - distance,
                    ))

            return SearchResponse(
                query=query,
                results=search_results,
                search_tier="semantic",
                total_matches=len(search_results),
            )
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            return SearchResponse(query=query, search_tier="semantic (error)")


class CodeSearch:
    """
    Unified search interface — tries Tier 1 first, falls back to Tier 2.
    """

    def __init__(self, root_dir: str = "."):
        self.ripgrep = RipgrepSearch(root_dir)
        self.semantic = SemanticSearch()

    def search(self, query: str, **kwargs) -> SearchResponse:
        """Search using the best available method."""
        # Try ripgrep first
        response = self.ripgrep.search(query, **kwargs)

        # If no results and semantic is available, try semantic
        if not response.has_results and self.semantic.is_available:
            semantic_response = self.semantic.search(query)
            if semantic_response.has_results:
                return semantic_response

        return response
