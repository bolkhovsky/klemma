# MCP Tool Integration

External tool access via Model Context Protocol (MCP). Servers are not installed by Klemma — only launch commands are registered in `config.yaml`.

## Modules

### client.py (133 lines)
`MCPClient` — sync wrapper around async MCP Python SDK (stdio transport).
- `list_tools()` → `ToolInfo` list (name, description, input_schema)
- `call_tool()` → `ToolResult` (content, is_error, raw)
- Each call spawns a fresh subprocess connection (stateless, no persistent session)
- Handles async-in-sync: detects running event loop, falls back to `ThreadPoolExecutor`

### registry.py (129 lines)
`ToolRegistry` — created once per `KlemmaContext`, manages MCP server configs from `config.mcp.servers`.
- `_get_client()` — lazy create or return cached `MCPClient`
- `call()` — route tool call to the right server
- `list_tools()` / `available_tools()` — enumerate server capabilities
- `add_server()` / `remove_server()` — edit `config.yaml` with regex (preserves formatting)

### discovery.py (262 lines)
Hybrid discovery pipeline for finding new literature:
- **Phase 1** (deterministic): MCP search per ref-gap + section keywords → deduplicate against existing library
- **Phase 2** (Claude): relevance assessment, usage type, priority scoring
- Results saved to `discoveries` table (status: pending/accepted/rejected)
- Can run as background subprocess: `python -m klemma.tools.discovery --section X.X --config config.yaml`

## Data flows

### MCP tool integration
`klemma tools add <name> --command <cmd> --args <args>` → writes to project `.klemma/config.yaml` (or `~/.klemma/config.yaml` with `--global` flag).
`klemma tools remove <name>` → removes from project config (or global with `--global`).
`klemma tools call <server> <tool> <args>` → `registry.call()` → `client.call_tool()` → MCP stdio.

### Paper search
`klemma search "query"` → `ToolRegistry.call("academia", "arxiv_search", ...)` → rich table output.
Requires registered `academia` MCP server.

### Discovery pipeline
`klemma discover -s X.X` → `discovery.run_discovery()` → Phase 1 + Phase 2 → `state.save_discovery()`.
Background mode: `--background` spawns subprocess; `--status` checks progress; `--review` shows results.

## Maintaining this file
Update when: adding new MCP server types, changing `MCPClient`/`ToolRegistry` interfaces, modifying discovery pipeline phases, or adding new tool-related CLI commands.

See: [Core infrastructure](../CLAUDE.md) for `ToolRegistry` in `KlemmaContext` | [AI Skills](../skills/CLAUDE.md) for how discovery feeds research briefings
