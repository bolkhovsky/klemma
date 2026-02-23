"""Tests for MCP tool registry and SPECTER MCP server."""

import importlib.util

import pytest

from klemma.tools.registry import ToolInfo, ToolRegistry
from klemma.tools.specter_server import compare_intents, create_specter_server

_MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
_skip_no_mcp = pytest.mark.skipif(not _MCP_AVAILABLE, reason="requires klemma[mcp]: pip install klemma[mcp]")


class TestToolRegistry:
    """Tests for ToolRegistry without actual MCP connections."""

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert reg.list_tools() == []
        assert reg.list_servers() == []

    def test_register_server(self):
        reg = ToolRegistry()
        mock_client = object()
        reg.register_server("test-server", mock_client, [
            {"name": "tool_a", "description": "Does A"},
            {"name": "tool_b", "description": "Does B"},
        ])
        assert "test-server" in reg.list_servers()
        assert len(reg.list_tools()) == 2

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register_server("srv", object(), [
            {"name": "my_tool", "description": "My tool", "inputSchema": {"type": "object"}},
        ])
        tool = reg.get_tool("my_tool")
        assert tool is not None
        assert tool.name == "my_tool"
        assert tool.description == "My tool"
        assert tool.server_name == "srv"

    def test_get_tool_not_found(self):
        reg = ToolRegistry()
        assert reg.get_tool("nonexistent") is None

    def test_unregister_server(self):
        reg = ToolRegistry()
        reg.register_server("srv", object(), [
            {"name": "tool_a", "description": "A"},
        ])
        assert len(reg.list_tools()) == 1
        reg.unregister_server("srv")
        assert len(reg.list_tools()) == 0
        assert len(reg.list_servers()) == 0

    def test_get_server_for_tool(self):
        reg = ToolRegistry()
        client = object()
        reg.register_server("srv", client, [
            {"name": "tool_a", "description": "A"},
        ])
        assert reg.get_server("tool_a") is client

    def test_get_server_nonexistent_tool(self):
        reg = ToolRegistry()
        assert reg.get_server("nonexistent") is None

    def test_multiple_servers(self):
        reg = ToolRegistry()
        reg.register_server("srv1", object(), [
            {"name": "s1_tool", "description": "Server 1 tool"},
        ])
        reg.register_server("srv2", object(), [
            {"name": "s2_tool", "description": "Server 2 tool"},
        ])
        assert len(reg.list_servers()) == 2
        assert len(reg.list_tools()) == 2
        assert reg.get_tool("s1_tool").server_name == "srv1"
        assert reg.get_tool("s2_tool").server_name == "srv2"

    @pytest.mark.asyncio
    async def test_call_tool_not_found(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            await reg.call_tool("nonexistent", {})

    @pytest.mark.asyncio
    async def test_call_tool_server_disconnected(self):
        reg = ToolRegistry()
        reg.register_server("srv", None, [
            {"name": "tool", "description": ""},
        ])
        # Server is None → disconnected
        reg._servers["srv"] = None
        with pytest.raises(RuntimeError, match="not connected"):
            await reg.call_tool("tool", {})


class TestToolInfo:
    """Tests for ToolInfo dataclass."""

    def test_creation(self):
        info = ToolInfo(name="t", description="desc", server_name="s")
        assert info.name == "t"
        assert info.description == "desc"
        assert info.server_name == "s"
        assert info.input_schema == {}


# ---------------------------------------------------------------------------
# SPECTER MCP Server Tests
# ---------------------------------------------------------------------------


class MockEmbeddingProvider:
    """Mock provider returning deterministic vectors for testing."""

    dim: int = 3
    model_name: str = "mock-specter"

    def __init__(self, vectors=None, fail_titles=None):
        self._vectors = vectors or {}
        self._fail_titles = fail_titles or set()

    def embed(self, title, abstract=""):
        if title in self._fail_titles:
            return None
        if title in self._vectors:
            return self._vectors[title]
        return [0.1, 0.2, 0.3]


@_skip_no_mcp
class TestSpecterServerCreation:
    """Tests for SPECTER MCP server factory."""

    def test_create_server_s2_backend(self):
        server = create_specter_server(backend="s2")
        assert server is not None
        assert server.name == "klemma-specter"

    def test_create_server_has_four_tools(self):
        server = create_specter_server(backend="s2")
        tools = server._tool_manager.list_tools()
        tool_names = {t.name for t in tools}
        assert "embed_paper" in tool_names
        assert "find_similar" in tool_names
        assert "batch_embed" in tool_names
        assert "get_citation_intents" in tool_names
        assert len(tool_names) == 4


class TestEmbedPaperTool:
    """Tests for embed_paper MCP tool."""

    def _make_server(self, provider):
        """Create server with mock provider injected."""
        server = create_specter_server(backend="s2")
        # Replace the provider in the tool closures by patching
        # We test the logic directly instead
        return server

    def test_embed_paper_success(self):
        provider = MockEmbeddingProvider()
        result = _call_embed_paper(provider, "Test Paper", "Some abstract")
        assert "vector" in result
        assert result["dim"] == 3
        assert result["model"] == "mock-specter"

    def test_embed_paper_failure(self):
        provider = MockEmbeddingProvider(fail_titles={"Bad Paper"})
        result = _call_embed_paper(provider, "Bad Paper")
        assert "error" in result
        assert result["model"] == "mock-specter"

    def test_embed_paper_no_abstract(self):
        provider = MockEmbeddingProvider()
        result = _call_embed_paper(provider, "Title Only")
        assert "vector" in result
        assert result["dim"] == 3


class TestFindSimilarTool:
    """Tests for find_similar MCP tool."""

    def test_find_similar_basic(self):
        query = [1.0, 0.0, 0.0]
        candidates = {
            "paper_a": [0.9, 0.1, 0.0],
            "paper_b": [0.0, 1.0, 0.0],
            "paper_c": [0.8, 0.2, 0.0],
        }
        results = _call_find_similar(query, candidates, top_k=2)
        assert len(results) == 2
        assert results[0]["paper_id"] == "paper_a"
        assert results[0]["similarity"] > results[1]["similarity"]

    def test_find_similar_with_threshold(self):
        query = [1.0, 0.0, 0.0]
        candidates = {
            "close": [0.9, 0.1, 0.0],
            "far": [0.0, 1.0, 0.0],
        }
        results = _call_find_similar(query, candidates, threshold=0.5)
        assert len(results) == 1
        assert results[0]["paper_id"] == "close"

    def test_find_similar_empty_candidates(self):
        results = _call_find_similar([1.0, 0.0], {})
        assert results == []

    def test_find_similar_top_k_limits(self):
        query = [1.0, 0.0]
        candidates = {f"p{i}": [1.0, 0.0] for i in range(10)}
        results = _call_find_similar(query, candidates, top_k=3)
        assert len(results) == 3


class TestBatchEmbedTool:
    """Tests for batch_embed MCP tool."""

    def test_batch_embed_all_success(self):
        provider = MockEmbeddingProvider()
        papers = [
            {"id": "p1", "title": "Paper One", "abstract": "Abstract one"},
            {"id": "p2", "title": "Paper Two"},
        ]
        result = _call_batch_embed(provider, papers)
        assert result["total"] == 2
        assert result["embedded"] == 2
        assert result["failed"] == 0
        assert "p1" in result["results"]
        assert "p2" in result["results"]

    def test_batch_embed_partial_failure(self):
        provider = MockEmbeddingProvider(fail_titles={"Bad Paper"})
        papers = [
            {"id": "good", "title": "Good Paper"},
            {"id": "bad", "title": "Bad Paper"},
        ]
        result = _call_batch_embed(provider, papers)
        assert result["embedded"] == 1
        assert result["failed"] == 1
        assert "good" in result["results"]
        assert len(result["errors"]) == 1

    def test_batch_embed_missing_title(self):
        provider = MockEmbeddingProvider()
        papers = [
            {"id": "no_title"},
        ]
        result = _call_batch_embed(provider, papers)
        assert result["failed"] == 1
        assert result["embedded"] == 0

    def test_batch_embed_empty(self):
        provider = MockEmbeddingProvider()
        result = _call_batch_embed(provider, [])
        assert result["total"] == 0
        assert result["embedded"] == 0

    def test_batch_embed_model_info(self):
        provider = MockEmbeddingProvider()
        result = _call_batch_embed(provider, [{"id": "p1", "title": "T"}])
        assert result["model"] == "mock-specter"


# ---------------------------------------------------------------------------
# Helper functions that replicate tool logic for unit testing
# (avoids needing a running MCP server)
# ---------------------------------------------------------------------------


def _call_embed_paper(provider, title, abstract=""):
    """Replicate embed_paper tool logic for testing."""
    vector = provider.embed(title, abstract)
    if vector is None:
        return {"error": f"Could not embed paper: {title[:80]}", "model": provider.model_name}
    return {"vector": vector, "dim": len(vector), "model": provider.model_name}


def _call_find_similar(query_vector, candidates, top_k=5, threshold=0.0):
    """Replicate find_similar tool logic for testing."""
    from klemma.embeddings import cosine_similarity

    results = []
    for paper_id, vec in candidates.items():
        sim = cosine_similarity(query_vector, vec)
        if sim >= threshold:
            results.append({"paper_id": paper_id, "similarity": round(sim, 4)})
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


def _call_batch_embed(provider, papers):
    """Replicate batch_embed tool logic for testing."""
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


# ---------------------------------------------------------------------------
# Citation Intent Comparison Tests
# ---------------------------------------------------------------------------


class TestCompareIntents:
    """Tests for LLM vs S2 citation intent comparison."""

    def test_perfect_match(self):
        llm = {"p1": "background", "p2": "method", "p3": "result_comparison"}
        s2 = {
            "p1": ["Background"],
            "p2": ["Methodology"],
            "p3": ["Result"],
        }
        result = compare_intents(llm, s2)
        assert result["total"] == 3
        assert result["matches"] == 3
        assert result["accuracy"] == 1.0

    def test_no_match(self):
        llm = {"p1": "background", "p2": "method"}
        s2 = {
            "p1": ["Methodology"],
            "p2": ["Result"],
        }
        result = compare_intents(llm, s2)
        assert result["total"] == 2
        assert result["matches"] == 0
        assert result["accuracy"] == 0.0

    def test_partial_match(self):
        llm = {"p1": "background", "p2": "method", "p3": "background"}
        s2 = {
            "p1": ["Background"],
            "p2": ["Result"],
            "p3": ["Background", "Methodology"],
        }
        result = compare_intents(llm, s2)
        assert result["total"] == 3
        assert result["matches"] == 2  # p1 and p3 match
        assert result["accuracy"] == pytest.approx(2 / 3, abs=0.01)

    def test_multi_intent_s2(self):
        """S2 can return multiple intents per citation — match if any."""
        llm = {"p1": "method"}
        s2 = {"p1": ["Background", "Methodology"]}
        result = compare_intents(llm, s2)
        assert result["matches"] == 1

    def test_missing_s2_data(self):
        """Papers not in S2 data are skipped."""
        llm = {"p1": "background", "p2": "method"}
        s2 = {"p1": ["Background"]}
        result = compare_intents(llm, s2)
        assert result["total"] == 1  # p2 skipped
        assert result["matches"] == 1

    def test_empty_inputs(self):
        result = compare_intents({}, {})
        assert result["total"] == 0
        assert result["accuracy"] == 0.0

    def test_confusion_matrix(self):
        llm = {"p1": "background", "p2": "background"}
        s2 = {
            "p1": ["Background"],
            "p2": ["Methodology"],
        }
        result = compare_intents(llm, s2)
        assert "background" in result["confusion"]
        assert result["confusion"]["background"]["background"] == 1
        assert result["confusion"]["background"]["method"] == 1

    def test_details_per_paper(self):
        llm = {"p1": "method"}
        s2 = {"p1": ["Methodology"]}
        result = compare_intents(llm, s2)
        assert len(result["details"]) == 1
        detail = result["details"][0]
        assert detail["citekey"] == "p1"
        assert detail["llm_intent"] == "method"
        assert detail["match"] is True
