"""Tool registry for managing MCP server connections.

Routes tool calls to the appropriate connected server.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolInfo:
    """Metadata for a registered tool."""

    name: str
    description: str
    server_name: str
    input_schema: dict = field(default_factory=dict)


class ToolRegistry:
    """Registry of MCP tools across multiple servers.

    Manages tool discovery and routing:
    - Register/unregister servers
    - List available tools
    - Route tool calls to the correct server
    """

    def __init__(self):
        self._servers: dict[str, Any] = {}  # name → MCPClient
        self._tools: dict[str, ToolInfo] = {}  # tool_name → ToolInfo

    def register_server(self, name: str, client: Any, tools: list[dict]):
        """Register an MCP server and its tools.

        Args:
            name: Unique server identifier
            client: MCPClient instance
            tools: List of tool dicts with 'name', 'description', 'inputSchema'
        """
        self._servers[name] = client
        for tool in tools:
            tool_name = tool["name"]
            self._tools[tool_name] = ToolInfo(
                name=tool_name,
                description=tool.get("description", ""),
                server_name=name,
                input_schema=tool.get("inputSchema", {}),
            )
        logger.info("Registered server '%s' with %d tools", name, len(tools))

    def unregister_server(self, name: str):
        """Remove a server and all its tools."""
        tools_to_remove = [
            t for t in self._tools.values() if t.server_name == name
        ]
        for tool in tools_to_remove:
            del self._tools[tool.name]
        self._servers.pop(name, None)
        logger.info("Unregistered server '%s' (%d tools removed)", name, len(tools_to_remove))

    def list_tools(self) -> list[ToolInfo]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_servers(self) -> list[str]:
        """List registered server names."""
        return list(self._servers.keys())

    def get_tool(self, name: str) -> Optional[ToolInfo]:
        """Get tool info by name."""
        return self._tools.get(name)

    def get_server(self, tool_name: str) -> Optional[Any]:
        """Get the MCPClient that owns a given tool."""
        info = self._tools.get(tool_name)
        if info:
            return self._servers.get(info.server_name)
        return None

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Route a tool call to the appropriate server.

        Raises KeyError if tool not found, RuntimeError if server disconnected.
        """
        info = self._tools.get(name)
        if not info:
            raise KeyError(f"Tool '{name}' not found in registry")

        client = self._servers.get(info.server_name)
        if not client:
            raise RuntimeError(
                f"Server '{info.server_name}' not connected for tool '{name}'"
            )

        return await client.call_tool(name, arguments)
