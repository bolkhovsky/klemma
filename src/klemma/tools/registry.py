"""ToolRegistry — manages MCP server configs and provides tool access.

Reads server definitions from KlemmaConfig.mcp.servers.
Lazily creates MCPClient instances on first use.
"""

import logging
from typing import Optional

from ..config import KlemmaConfig
from .client import MCPClient, ToolInfo, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of MCP tool servers. Created once per CLI command via KlemmaContext."""

    def __init__(self, config: KlemmaConfig):
        self._config = config
        self._clients: dict[str, MCPClient] = {}

    @property
    def servers(self) -> list[str]:
        """List registered server names."""
        return list(self._config.mcp.servers.keys())

    def has(self, server: str) -> bool:
        """Check if a server is registered."""
        return server in self._config.mcp.servers

    def _get_client(self, server: str) -> MCPClient:
        """Get or create MCPClient for a server."""
        if server not in self._clients:
            if server not in self._config.mcp.servers:
                raise KeyError(f"MCP server '{server}' not registered. Use: klemma tools add {server}")
            srv = self._config.mcp.servers[server]
            self._clients[server] = MCPClient(
                command=srv.command,
                args=srv.args,
                env=srv.env,
            )
        return self._clients[server]

    def list_tools(self, server: str) -> list[ToolInfo]:
        """List tools available on a server."""
        client = self._get_client(server)
        return client.list_tools()

    def call(self, server: str, tool: str, args: Optional[dict] = None) -> ToolResult:
        """Call a tool on a server."""
        client = self._get_client(server)
        return client.call_tool(tool, args)

    def available_tools(self, server: str) -> list[str]:
        """List tool names available on a server."""
        return [t.name for t in self.list_tools(server)]


def _render_server_yaml(name: str, command: str, args: list[str], env: dict[str, str]) -> str:
    """Render a single server entry as YAML text."""
    lines = [f"    {name}:"]
    lines.append(f'      command: "{command}"')
    if args:
        lines.append("      args:")
        for a in args:
            lines.append(f'        - "{a}"')
    if env:
        lines.append("      env:")
        for k, v in env.items():
            lines.append(f'        {k}: "{v}"')
    return "\n".join(lines)


def add_server(config_path: str, name: str, command: str, args: list[str], env: dict[str, str]):
    """Add an MCP server to config.yaml (preserves existing formatting)."""
    import re

    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()

    server_block = _render_server_yaml(name, command, args, env)

    # Check if mcp.servers section exists
    if re.search(r"^mcp:\s*\n\s+servers:", text, re.MULTILINE):
        # Check if this server already exists — replace it
        pattern = rf"^(\s+){re.escape(name)}:\s*\n(?:\1\s+.*\n)*"
        if re.search(pattern, text, re.MULTILINE):
            # Remove old entry, then append new
            text = re.sub(pattern, "", text, flags=re.MULTILINE)

        # Append server under existing servers: block
        text = re.sub(
            r"(^mcp:\s*\n\s+servers:\s*\n)",
            rf"\1{server_block}\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        # Append entire mcp section at the end
        text = text.rstrip() + f"\n\nmcp:\n  servers:\n{server_block}\n"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info("Added MCP server '%s' to %s", name, config_path)


def remove_server(config_path: str, name: str) -> bool:
    """Remove an MCP server from config.yaml (preserves existing formatting)."""
    import re

    with open(config_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Match the server block: "    name:" followed by indented lines
    pattern = rf"^    {re.escape(name)}:.*\n(?:      .*\n|        .*\n)*"
    new_text = re.sub(pattern, "", text, flags=re.MULTILINE)
    if text == new_text:
        return False

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_text)

    logger.info("Removed MCP server '%s' from %s", name, config_path)
    return True
