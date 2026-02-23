"""Minimal MCP client for connecting to MCP servers.

Requires: pip install klemma[mcp]
Uses the `mcp` package for stdio transport.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _ensure_mcp():
    """Lazy import of mcp package."""
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        raise ImportError(
            "Install klemma[mcp] for MCP support: pip install klemma[mcp]"
        )


class MCPClient:
    """Minimal MCP client that connects to a single server via stdio.

    Lifecycle:
        client = MCPClient("my-server", "uvx", ["my-mcp-server"])
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
        await client.disconnect()
    """

    def __init__(self, name: str, command: str, args: list[str] = None, env: dict = None):
        """Initialize client.

        Args:
            name: Human-readable server name
            command: Server executable (e.g. "uvx", "npx", "python")
            args: Command arguments (e.g. ["my-mcp-server"])
            env: Optional environment variables for the server process
        """
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self._session = None
        self._read = None
        self._write = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> list[dict]:
        """Connect to server, initialize session, return available tools.

        Returns list of tool dicts: [{"name": ..., "description": ..., "inputSchema": ...}]
        """
        _ensure_mcp()
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
        )

        self._read, self._write = await stdio_client(server_params).__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

        tools_result = await self._session.list_tools()
        tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
            }
            for t in tools_result.tools
        ]
        logger.info("Connected to '%s': %d tools available", self.name, len(tools))
        return tools

    async def list_tools(self) -> list[dict]:
        """List available tools from the connected server."""
        if not self._session:
            raise RuntimeError(f"Client '{self.name}' not connected")
        result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema if hasattr(t, "inputSchema") else {},
            }
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool on the connected server.

        Returns the tool result content.
        """
        if not self._session:
            raise RuntimeError(f"Client '{self.name}' not connected")
        result = await self._session.call_tool(name, arguments)
        return result

    async def disconnect(self):
        """Disconnect from the server."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        self._read = None
        self._write = None
        logger.info("Disconnected from '%s'", self.name)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()
