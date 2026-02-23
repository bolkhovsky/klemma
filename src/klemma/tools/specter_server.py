"""SPECTER embedding MCP server for Klemma.

Provides embedding and citation analysis tools over MCP protocol:
- embed_paper: Embed a single paper by title + abstract
- find_similar: Find similar papers using cosine similarity
- batch_embed: Embed multiple papers in one call
- get_citation_intents: Fetch citation intents from Semantic Scholar API

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

    @server.tool(
        name="get_citation_intents",
        description=(
            "Fetch citation intents for a paper from Semantic Scholar API. "
            "Returns how other papers cite the given paper, with intent labels "
            "(Background, Methodology, Result) and citation contexts."
        ),
    )
    def get_citation_intents(
        paper_id: str,
        limit: int = 100,
    ) -> dict:
        """Fetch citation intents from S2 API.

        Args:
            paper_id: S2 paper ID, DOI, or title query
            limit: Maximum citations to fetch (default 100)

        Returns:
            Dict with 'citations' list and 'stats' (intent distribution).
        """
        return fetch_citation_intents(paper_id, limit=limit)

    return server


# ---------------------------------------------------------------------------
# S2 Citation Intents API
# ---------------------------------------------------------------------------


def fetch_citation_intents(paper_id: str, limit: int = 100) -> dict:
    """Fetch citation intents from Semantic Scholar API.

    Uses GET /paper/{paper_id}/citations?fields=intents,contexts,title

    Args:
        paper_id: S2 paper ID, DOI, or arXiv ID
        limit: Max citations to return

    Returns:
        Dict with 'citations' and 'stats' keys.
    """
    import requests

    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}/citations"
    params = {
        "fields": "intents,contexts,title",
        "limit": min(limit, 1000),
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e), "citations": [], "stats": {}}

    citations = []
    intent_counts = {"Background": 0, "Methodology": 0, "Result": 0}

    for item in data.get("data", []):
        citing = item.get("citingPaper", {})
        intents = item.get("intents", [])
        contexts = item.get("contexts", [])

        for intent in intents:
            if intent in intent_counts:
                intent_counts[intent] += 1

        citations.append({
            "title": citing.get("title", ""),
            "paperId": citing.get("paperId", ""),
            "intents": intents,
            "contexts": contexts[:3],  # Limit context snippets
        })

    return {
        "citations": citations,
        "stats": intent_counts,
        "total": len(citations),
    }


# ---------------------------------------------------------------------------
# Intent Comparison: LLM-predicted vs S2 ground truth
# ---------------------------------------------------------------------------

# Mapping from klemma intent labels to S2 intent labels
_KLEMMA_TO_S2 = {
    "background": "Background",
    "method": "Methodology",
    "result_comparison": "Result",
}

_S2_TO_KLEMMA = {v: k for k, v in _KLEMMA_TO_S2.items()}


def compare_intents(
    llm_intents: dict[str, str],
    s2_intents: dict[str, list[str]],
) -> dict:
    """Compare LLM-predicted citation intents against S2 ground truth.

    Args:
        llm_intents: {citekey: "background"|"method"|"result_comparison"}
            from klemma's LLM extraction
        s2_intents: {citekey: ["Background", "Methodology", "Result"]}
            from S2 API (a paper can have multiple intents)

    Returns:
        Dict with accuracy metrics:
        - total: number of papers compared
        - matches: correct predictions
        - accuracy: matches / total
        - confusion: {predicted: {actual: count}}
        - details: per-paper comparison
    """
    total = 0
    matches = 0
    confusion: dict[str, dict[str, int]] = {}
    details = []

    for citekey, llm_intent in llm_intents.items():
        s2_labels = s2_intents.get(citekey, [])
        if not s2_labels:
            continue

        total += 1
        s2_mapped = _KLEMMA_TO_S2.get(llm_intent, "")

        # S2 returns a list — match if LLM prediction is among them
        is_match = s2_mapped in s2_labels
        if is_match:
            matches += 1

        # Confusion matrix
        predicted = llm_intent
        for s2_label in s2_labels:
            actual = _S2_TO_KLEMMA.get(s2_label, s2_label)
            confusion.setdefault(predicted, {})
            confusion[predicted].setdefault(actual, 0)
            confusion[predicted][actual] += 1

        details.append({
            "citekey": citekey,
            "llm_intent": llm_intent,
            "s2_intents": s2_labels,
            "match": is_match,
        })

    return {
        "total": total,
        "matches": matches,
        "accuracy": round(matches / total, 4) if total > 0 else 0.0,
        "confusion": confusion,
        "details": details,
    }


def main():
    """Run the SPECTER MCP server as a standalone process."""
    backend = "local" if "--local" in sys.argv else "s2"
    server = create_specter_server(backend=backend)
    server.run()


if __name__ == "__main__":
    main()
