"""SPECTER embedding MCP server for Klemma.

Provides embedding tools over MCP protocol:
- embed_paper: Embed a single paper by title + abstract
- find_similar: Find similar papers using cosine similarity
- batch_embed: Embed multiple papers in one call

Backends: Semantic Scholar API (default) or local sentence-transformers.

Usage:
    python -m klemma.tools.specter_server          # S2 backend (default)
    python -m klemma.tools.specter_server --local   # local SPECTER2

Requires: pip install klemma[mcp]
"""

import logging
import sys

logger = logging.getLogger(__name__)


def _ensure_deps():
    """Verify MCP package is available."""
    try:
        import mcp  # noqa: F401

        return True
    except ImportError:
        raise ImportError(
            "Install klemma[mcp] for MCP support: pip install klemma[mcp]"
        )


def create_specter_server(backend: str = "s2", **backend_kwargs):
    """Create an MCP server with SPECTER embedding tools.

    Args:
        backend: "s2" (Semantic Scholar API) or "local" (sentence-transformers)
        **backend_kwargs: Passed to the embedding provider constructor

    Returns:
        FastMCP server instance
    """
    _ensure_deps()
    from mcp.server.fastmcp import FastMCP

    from klemma.embeddings import (
        LocalSPECTEREmbeddings,
        SemanticScholarEmbeddings,
        cosine_similarity,
    )

    # Create embedding provider
    if backend == "local":
        provider = LocalSPECTEREmbeddings(**backend_kwargs)
    else:
        provider = SemanticScholarEmbeddings(**backend_kwargs)

    server = FastMCP(
        "klemma-specter",
        instructions=(
            "SPECTER embedding server for academic papers. "
            "Provides tools for computing paper embeddings and finding similar papers."
        ),
    )

    @server.tool(
        name="embed_paper",
        description=(
            "Compute a SPECTER embedding for an academic paper. "
            "Returns a float vector suitable for cosine similarity comparisons."
        ),
    )
    def embed_paper(title: str, abstract: str = "") -> dict:
        """Embed a paper by title and optional abstract.

        Args:
            title: Paper title
            abstract: Paper abstract (optional, improves quality)

        Returns:
            Dict with 'vector' (list[float]), 'dim' (int), 'model' (str)
            or 'error' if embedding failed.
        """
        vector = provider.embed(title, abstract)
        if vector is None:
            return {
                "error": f"Could not embed paper: {title[:80]}",
                "model": provider.model_name,
            }
        return {
            "vector": vector,
            "dim": len(vector),
            "model": provider.model_name,
        }

    @server.tool(
        name="find_similar",
        description=(
            "Find the most similar papers from a set of candidates "
            "using cosine similarity of SPECTER embeddings."
        ),
    )
    def find_similar(
        query_vector: list[float],
        candidates: dict[str, list[float]],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> list[dict]:
        """Find similar papers by comparing embedding vectors.

        Args:
            query_vector: The query embedding vector
            candidates: Dict mapping paper_id → embedding vector
            top_k: Maximum results to return (default 5)
            threshold: Minimum similarity score (default 0.0)

        Returns:
            List of {paper_id, similarity} sorted by similarity descending.
        """
        results = []
        for paper_id, vec in candidates.items():
            sim = cosine_similarity(query_vector, vec)
            if sim >= threshold:
                results.append({"paper_id": paper_id, "similarity": round(sim, 4)})
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    @server.tool(
        name="batch_embed",
        description=(
            "Embed multiple papers in a single call. "
            "Returns a mapping from paper_id to embedding vector."
        ),
    )
    def batch_embed(papers: list[dict]) -> dict:
        """Embed a batch of papers.

        Args:
            papers: List of dicts with 'id', 'title', and optional 'abstract'

        Returns:
            Dict with 'results' mapping id → {vector, dim} and 'errors' list.
        """
        results = {}
        errors = []
        for paper in papers:
            paper_id = paper.get("id", paper.get("title", "unknown"))
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            if not title:
                errors.append({"id": paper_id, "error": "Missing title"})
                continue
            vector = provider.embed(title, abstract)
            if vector is None:
                errors.append({"id": paper_id, "error": "Embedding failed"})
            else:
                results[paper_id] = {"vector": vector, "dim": len(vector)}

        return {
            "results": results,
            "errors": errors,
            "model": provider.model_name,
            "total": len(papers),
            "embedded": len(results),
            "failed": len(errors),
        }

    return server


def main():
    """Run the SPECTER MCP server as a standalone process."""
    backend = "local" if "--local" in sys.argv else "s2"
    server = create_specter_server(backend=backend)
    server.run()


if __name__ == "__main__":
    main()
