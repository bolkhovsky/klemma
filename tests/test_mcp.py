"""Tests for MCP tool registry (no actual MCP server needed)."""

import pytest

from klemma.tools.registry import ToolInfo, ToolRegistry


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
