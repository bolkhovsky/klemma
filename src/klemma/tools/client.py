"""MCPClient — synchronous wrapper around the MCP Python SDK.

Handles subprocess lifecycle, stdio transport, and tool invocation.
Each call_tool / list_tools spawns a fresh connection (stateless).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logger = logging.getLogger(__name__)


@dataclass
class ToolInfo:
    """Metadata for a single MCP tool."""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result of a tool invocation."""

    content: Any = None
    is_error: bool = False
    raw: Any = None


class MCPClient:
    """Sync MCP client. One instance per server config.

    Usage::

        client = MCPClient(command="uvx", args=["zotero-mcp"], env={"ZOTERO_LOCAL": "true"})
        tools = client.list_tools()
        result = client.call_tool("zotero_search_items", {"query": "ice forecast"})
    """

    def __init__(
        self,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.timeout = timeout

    def _server_params(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env if self.env else None,
        )

    async def _run_session(self, callback):
        """Connect, initialize, run callback, disconnect."""
        params = self._server_params()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await callback(session)

    def _run(self, callback):
        """Run an async session callback synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — use a new thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._run_session(callback))
                return future.result(timeout=self.timeout)
        else:
            return asyncio.run(self._run_session(callback))

    def list_tools(self) -> list[ToolInfo]:
        """List all tools available on this server."""

        async def _list(session: ClientSession):
            result = await session.list_tools()
            return [
                ToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                )
                for tool in result.tools
            ]

        try:
            return self._run(_list)
        except Exception as e:
            logger.error("Failed to list tools from %s: %s", self.command, e)
            return []

    def call_tool(self, tool_name: str, arguments: Optional[dict] = None) -> ToolResult:
        """Call a tool on this server and return the result."""

        async def _call(session: ClientSession):
            result = await session.call_tool(tool_name, arguments or {})
            # Extract text content from result
            content = None
            if result.content:
                texts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        texts.append(item.text)
                content = "\n".join(texts) if texts else str(result.content)
            return ToolResult(
                content=content,
                is_error=bool(result.isError) if hasattr(result, "isError") else False,
                raw=result,
            )

        try:
            return self._run(_call)
        except Exception as e:
            logger.error("Failed to call tool %s on %s: %s", tool_name, self.command, e)
            return ToolResult(content=str(e), is_error=True)
