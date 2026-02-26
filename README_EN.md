<div align="center">

```
    /\  /\
   ( o  o )   Klemma
   (  >>  )   AI Academic Assistant
    / || \
   (_/  \_)
```

# Klemma

AI-powered CLI assistant for PhD dissertation research.

</div>

Klemma manages your literature library (via Zotero), extracts citation fragments from PDFs using AI (Claude, OpenAI, Ollama, LiteLLM), generates daily plans, research briefings, library analysis, and tracks dissertation coverage across chapters and sections.

## Installation

```bash
pip install -e .
```

Requirements:
- Python 3.11+
- An AI backend (one of):
  - [Claude Code](https://claude.com/claude-code) CLI (`claude` in PATH) — default
  - `pip install klemma[openai]` — OpenAI API / Ollama / vLLM / LM Studio
  - `pip install klemma[litellm]` — 100+ providers via LiteLLM
- An Obsidian vault with `@citekey.md` source notes
- Zotero with BetterBibTeX (for PDF lookup)

## Quick Start

```bash
# 1. Set up your config
klemma init                          # creates ~/.klemma/ with templates
klemma init --outline                 # generate outline after init (requires AI)
# Edit ~/.klemma/config.yaml         — Zotero/Obsidian paths, AI backend
# Edit ~/.klemma/context.md          — your dissertation topic and structure
# Edit ~/.klemma/tags.yaml           — your domain tag taxonomy

# 2. Check status
klemma status                        # sources, coverage, gaps

# 3. Process sources (extract citation fragments from PDFs)
klemma process                       # all pending sources (parallel)
klemma process smithIceForecast2023  # specific source

# 4. Daily planning
klemma plan                          # AI-generated daily focus + briefing

# 5. Research briefing for a section
klemma research -s 1.3.2             # argument structure + citation plan

# 6. Library analysis
klemma library                       # health assessment
klemma library -s 2.3                # section reading recommendations
klemma library --audit               # deep quality audit
```

## Commands

| Command | Description |
|---------|-------------|
| `klemma init` | Scaffold `~/.klemma/` with config templates (use `--outline` to generate outline) |
| `klemma plan` | Generate daily focus and briefing |
| `klemma status` | Coverage stats, gaps, reference gaps |
| `klemma process [citekeys...]` | Extract citation fragments from PDFs |
| `klemma research -s X.X` | Deep section analysis with citation plan |
| `klemma library` | AI library analysis (status/recommend/audit) |
| `klemma ask "query"` | Research agent with full dissertation context |
| `klemma acquire <url>` | Download PDF, add to Zotero, register |
| `klemma search "query"` | Search papers via MCP (arXiv, Semantic Scholar) |
| `klemma discover -s X.X` | Automated literature discovery pipeline |
| `klemma tools {add,list,remove}` | Manage MCP tool servers |

## Configuration

User data lives in `~/.klemma/` (override with `KLEMMA_HOME` env var):

```
~/.klemma/
├── config.yaml    — main config (Zotero, Obsidian, AI, dissertation structure)
├── context.md     — dissertation context (topic, results, chapters, key terms)
├── tags.yaml      — tag taxonomy for fragment classification
├── prompts/       — optional overrides for shipped prompt templates
└── data/
    └── klemma.db  — SQLite database
```

Run `klemma init` to create this from shipped templates. See `config.example.yaml` for all available options.

### AI backends

```yaml
# ~/.klemma/config.yaml
ai:
  backend: "claude"     # default — uses Claude Code CLI
  model: "sonnet"
  language: "en"        # AI response language (en, ru, de, etc.)

# Or use OpenAI-compatible API:
ai:
  backend: "openai"
  model: "gpt-4o"
  base_url: "http://localhost:11434/v1"  # for Ollama
  api_key_env: "OPENAI_API_KEY"
```

### Prompt customization

Klemma ships English prompt templates in `prompts/`. To customize:
1. Copy any prompt to `~/.klemma/prompts/<name>.md`
2. Edit as needed — user overrides take priority over shipped prompts
3. Set `ai.language` in config to control AI response language

## Architecture

```
klemma (CLI)
├── AI Provider ─── pluggable backend for all AI calls
│   ├── ClaudeClient ── Claude Code CLI (default)
│   ├── OpenAIClient ── OpenAI / Ollama / vLLM / LM Studio
│   └── LiteLLMClient ─ 100+ providers
├── MCP Tool Layer ─ plug-and-play external servers
│   ├── zotero-mcp ─── Zotero library access
│   └── academia-mcp ─ arXiv, Semantic Scholar
├── LibraryProvider ─ swappable library backend
│   ├── LocalLibrary ── BetterBibTeX JSON (default)
│   └── MCPLibrary ──── zotero-mcp server
├── Obsidian vault ── source notes + research + daily notes
├── PyMuPDF ───────── PDF text extraction
└── SQLite ────────── sources, fragments, gaps, plans, queue
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

See [CLAUDE.md](CLAUDE.md) for detailed architecture docs, module descriptions, and data flows.

## License

MIT — see [LICENSE](LICENSE).
