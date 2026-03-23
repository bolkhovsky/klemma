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

Klemma manages your literature library (via Zotero), extracts citation fragments from PDFs using AI (Claude, OpenAI, Ollama, LiteLLM), classifies citation intent, generates daily plans, research briefings, library analysis, semantic search for similar sources, and tracks chapter coverage. Fragment RAG grounds answers in real citations from your library. Supports nested projects (dissertation + papers) with separate databases and resource inheritance.

## Installation

```bash
cd ~/projects/klemma
pip install -e .
```

Requirements:
- Python 3.11+
- An AI backend (one of):
  - `pip install klemma[recommended]` — LiteLLM: 100+ providers (recommended)
  - `pip install klemma[openai]` — OpenAI API / Ollama / vLLM / LM Studio
  - Claude Code CLI (`claude` in PATH) — no extra packages needed
- An Obsidian vault with source notes
- Zotero with BetterBibTeX plugin (JSON auto-export)

### Optional dependencies

```bash
pip install klemma[recommended]        # LiteLLM — recommended AI backend
pip install klemma[embeddings]         # semantic search (S2/OpenAI backends)
pip install klemma[local-embeddings]   # offline SPECTER2 (sentence-transformers)
pip install klemma[mcp]                # MCP servers (extensibility)
pip install klemma[all-ai]             # all AI backends (openai + litellm)
```

## Quick Start

```bash
# 1. Initialize a project
klemma init                                    # interactive wizard
klemma init --type paper                       # paper project
klemma init --outline                          # generate outline after init (requires AI)

# 2. Check stats, coverage, gaps
klemma status                                  # compact overview
klemma status --verbose                        # full tables + intent matrix

# 3. Process sources (fragments + citation intent + vault note)
klemma process                                 # all pending (parallel)
klemma process smithMachineLearning2020        # single source

# 4. Generate embeddings for semantic search
klemma embed                                   # all sources with abstracts
klemma similar smithMachineLearning2020        # similar sources
klemma similar 2.3                             # similar for section 2.3

# 5. Research briefing for a section
klemma research -s 1.3.2

# 6. AI library analysis
klemma library                                 # health assessment
klemma library -s 2.3                          # recommendations
klemma library --audit                         # audit + citation graph

# 7. Ask the research agent (Fragment RAG)
klemma ask "What are the main methods for validating ice forecasts?"

# 8. Project structure
klemma outline                                 # AI-generated outline
klemma outline -p "Focus on methodology"       # with a directive

# 9. Guided Serendipity — research branching points
klemma briefing goessling2016                  # analyze new source → forks
klemma insights                                # blind spots + hidden clusters
klemma decisions trail                         # trail of research decisions
```

## Guided Serendipity

AI-assisted research methodology where the system discovers unexpected connections in your library and presents explicit branching points (forks) for you to choose from. Your accumulated choices form a unique Research Trail.

### `klemma briefing <citekey>`
Analyze a new source: key claims, library connections, niches, 2-3 fork options.

```bash
klemma briefing goessling2016              # briefing for one source
klemma briefing --pending                  # top-10 unbriefed (by relevance)
klemma briefing --pending -n 5             # top-5
```

### `klemma insights`
Library analysis without AI — pure SQL + embeddings:

- **Blind spots** — sections with source count <50% of average
- **Hidden clusters** — semantically similar sources from different sections

```bash
klemma insights                            # table + save as decisions
```

### `klemma decisions`
View and manage research decisions:

```bash
klemma decisions                           # list all decisions
klemma decisions --pending                 # only awaiting response
klemma decisions show 5                    # details of decision #5
klemma decisions trail                     # chronological trail
klemma decide 5 B --reason "Closer to IIEE"  # choose option B
```

## Commands (20)

### `klemma init`
Initialize a project in the current directory. Creates `.klemma/` (config, tags, DB) and `KLEMMA.md` (AI context). The interactive wizard auto-discovers Obsidian vaults and Zotero exports.

```bash
klemma init                    # interactive wizard
klemma init --type paper       # paper project (instead of dissertation)
klemma init --no-input         # non-interactive (defaults)
klemma init --outline          # generate outline after init (requires AI)
```

### `klemma plan`
Daily plan: focus of the day, reading recommendations, assistant task, strategic suggestions. Considers yesterday's plan, chapter coverage, gaps, and deadlines. Plan is saved to DB and Obsidian daily note.

### `klemma status`
Unified command for processing stats, coverage, and gaps. Shows: processed/pending/failed sources, chapter coverage, under-covered sections, reference gaps with intent-weighted scoring.

```bash
klemma status                  # compact overview
klemma status --verbose        # full tables:
                               #   intent coverage matrix (background/method/result)
                               #   embedding stats
                               #   citation graph stats
klemma status --chapter 2      # filter by chapter
```

### `klemma process [<citekeys>...]`
Full processing pipeline: PDF → text (PyMuPDF) → AI analysis → SQLite + vault note.

Processing automatically:
- Creates vault note `@citekey.md` (AI annotation: summary, methodology, key references)
- Extracts fragments with **citation intent** classification (background / method / result_comparison)
- Records reference gaps (bibliography references missing from library)
- Builds citation graph (all references in `citation_links`)
- Auto-generates embedding (if embeddings backend is configured)

```bash
klemma process                                 # batch: all pending (3 threads)
klemma process smithML2020 jonesNLP2019        # specific sources
klemma process --serial                        # sequential (saves API calls)
```

### `klemma embed [<citekey>]`
Generate SPECTER/OpenAI embeddings for semantic search. Without arguments — backfill for all completed sources with abstracts.

```bash
klemma embed                                   # all without embeddings
klemma embed smithMachineLearning2020          # single source
klemma embed --dry-run                         # how many would be processed
klemma embed --backend local                   # override backend
```

### `klemma similar <citekey|section>`
Semantic search for similar sources via embedding cosine similarity.

```bash
klemma similar smithML2020                     # similar to this source
klemma similar 2.3                             # close to section 2.3 centroid
klemma similar smithML2020 -k 20               # top-20 results
```

When searching by section, shows sources from **other** sections semantically close to the given one — helps discover hidden connections.

### `klemma research -s <X.X>`
Research briefing: deep analysis of section readiness for writing. Automatically extracts fragments, gathers context (draft, fragments, coverage) and generates argumentation structure with a citation plan.

On repeat runs — incremental mode: reads user notes from `## Notes`, determines delta, and updates the briefing.

Token-aware prompt budget (~20K tokens): automatically reduces context (draft → summaries → fragment text → source count → fragment count) when exceeding limits. Uses RAG-first fragment search via semantic embedding (falls back to section-based when <10 results).

```bash
klemma research -s 1.3.2                       # first run: full analysis
klemma research -s 1.3.2                       # repeat: incremental update
klemma research -s 1.3.2 --force               # re-extract all fragments
```

### `klemma outline`
AI-generated project structure from directory contents + database + KLEMMA.md. Incremental update on repeat runs.

```bash
klemma outline                                 # AI generation
klemma outline -p "Focus on KG approaches"     # with a directive
klemma outline --fresh                         # full regeneration
klemma outline --scan-only                     # scan files only
```

### `klemma library [-s <X.X>] [--audit]`
AI library analysis. Three modes:

- **status** (default) — health: coverage, quality, issues
- **recommend** (`-s 2.3`) — reading recommendations for a section
- **audit** (`--audit`) — deep audit: duplicates, outdated sources, methodology gaps, **co-citation analysis**, **author network**, prune recommendations

```bash
klemma library                                 # health
klemma library -s 2.3                          # section recommendations
klemma library --audit                         # deep audit

klemma library prune                           # view prune recommendations
klemma library prune -v drop                   # only "drop" verdicts
klemma library prune --clear smithML2020       # clear verdict
```

### `klemma ask "query"`
Research agent with full project context and **Fragment RAG**: semantically searches relevant fragments from processed PDFs and injects them into the prompt. Answers are grounded in real citations from your library, not the model's general knowledge. Without fragment embeddings — works as before (metadata-only).

```bash
klemma ask "What are the main forecast validation methods?"
klemma ask -s 1.3.2 "Find papers about AMSR2"
klemma ask -ch 2 "Compare IceNet and ConvLSTM architectures"
```

### `klemma acquire <url>`
Download PDF and register in the database. For bulk import — `--batch` with a JSON file.

```bash
klemma acquire https://arxiv.org/pdf/2101.12345.pdf
klemma acquire <url> --title "Paper" --authors "Smith, J." --year 2023
klemma acquire --batch papers.json             # bulk import
klemma acquire <url> --no-process              # don't extract fragments
```

### `klemma info`
Current project: root directory, project chain, configuration, DB path.

### `klemma tree`
Nested project tree from current root.

### `klemma benchmark`
Quality evaluation framework: intent classification, gap ranking, embedding retrieval, citation reconstruction. Supports run history, comparison, autonomous pipeline, and ablation experiments.

```bash
klemma benchmark --export dataset.json          # dataset template from DB
klemma benchmark -d dataset.json --metrics all  # all metrics
klemma benchmark -d dataset.json --semantic     # hybrid keyword x semantic
klemma benchmark --analyst smithML2020          # extract ground truth from PDF
klemma benchmark -d dataset.json --reconstruct  # citation reconstruction
klemma benchmark --candidates                   # benchmark candidate papers
klemma benchmark --prepare smithML2020          # fetch missing references
klemma benchmark --auto                         # full autonomous pipeline
klemma benchmark --history                      # run history
klemma benchmark --compare id1 id2              # compare two runs

# Ablation parameters
klemma benchmark --auto --temperature 0.5       # override temperature
klemma benchmark --auto --max-recs 3            # max recommendations per section
klemma benchmark --auto --fragments 10          # fragments per source
klemma benchmark --auto --prompt-variant fewshot # few-shot prompt
```

### `klemma migrate [--dry-run]`
Migration from old format (`~/.klemma/`) to per-directory project. Splits config into system (AI) and project (everything else), copies context.md → KLEMMA.md.

### Backward-compatible aliases

Old names work as hidden aliases: `morning`→`plan`, `extract`→`process`, `agent`→`ask`, `stats`/`coverage`/`gaps`→`status`, `prepopulate`→`import`.

## Configuration

Three-level: `~/.klemmarc.yaml` (global) → `~/.klemma/config.yaml` (system) → `.klemma/config.yaml` (project). Nested projects inherit `obsidian`, `zotero`, `ai`, `embeddings` from parent.

### Global config (`~/.klemmarc.yaml`)

Created automatically on first `klemma init` (permissions 0600). Contains API keys and AI settings shared across all projects.

```yaml
ai:
  backend: "litellm"           # "litellm" (default) | "claude" | "openai"
  model: "anthropic/claude-sonnet-4-20250514"  # provider/model format
  timeout: 180                 # AI call timeout (sec)
  language: "en"               # AI response language ("en", "ru", "de", ...)
  # json_mode: true            # structured JSON output (if backend supports)
  # base_url: "http://localhost:11434/v1"  # for Ollama/vLLM/LM Studio

api_keys:
  anthropic: "sk-ant-..."      # for anthropic/* models
  openai: "sk-..."             # for openai/* models
  # google: "..."              # for gemini/* models
```

### System config (`~/.klemma/config.yaml`)

Alternative location for AI settings (legacy). Overridden by `~/.klemmarc.yaml`.

```yaml
ai:
  backend: "litellm"           # "litellm" (default) | "claude" | "openai"
  model: "anthropic/claude-sonnet-4-20250514"
  timeout: 180
  language: "en"
```

### Project config (`.klemma/config.yaml`)

```yaml
obsidian:
  vault_path: "/path/to/vault"
  notes_folder: "2 - Refs"     # folder with @citekey.md notes
  tags_folder: "3 - Tags"

zotero:
  library_json: "/path/to/bbt-export.json"   # BetterBibTeX JSON auto-export

embeddings:
  backend: "s2"                # "s2" (free S2 API) | "local" | "openai" | ""
  # model: "specter2"          # model name (depends on backend)
  # throttle: 3.1              # seconds between S2 API requests
  # api_key_env: "OPENAI_API_KEY"  # for OpenAI backend

project:
  type: "dissertation"         # "dissertation" | "paper" | "thesis"
  title: "Your dissertation title"
  chapters:
    1: "Literature Review"
    2: "Methodology"
    3: "Results"
  chapter_mapping:             # regex → chapter/section
    - pattern: "icenet|ice.?net"
      chapter: 2
      section: "2.3.1"
  min_sources_per_section: 3

state:
  db_path: "./data/klemma.db"
```

`zotero.library_json` — path to BetterBibTeX JSON export. PDFs are resolved in 3 steps: direct DB path → BetterBibTeX lookup (citekey → attachment path) → fuzzy filename search in Zotero storage.

### Embedding backends

| Backend | Dimensions | Cost | Requirements |
|---------|-----------|------|--------------|
| `s2` | 768 (SPECTER) | Free | Internet, throttle 3.1s |
| `local` | 768 (SPECTER2) | Free | `klemma[local-embeddings]`, GPU recommended |
| `openai` | 1536 | Paid | `klemma[openai]`, API key |

## Nested Projects

Klemma supports Git/NPM-style nesting. Each project has its own DB, but vault and Zotero are inherited from parent.

```
thesis_dir/
├── KLEMMA.md           # dissertation context
├── .klemma/            # dissertation DB
├── paper_ice/
│   ├── KLEMMA.md       # paper context (AI sees both)
│   └── .klemma/        # paper DB (inherits vault/zotero)
└── paper_climate/
    ├── KLEMMA.md
    └── .klemma/
```

```bash
cd thesis_dir/paper_ice/
klemma status                  # paper DB, dissertation vault
klemma info                    # show project chain
klemma tree                    # nesting tree
```

## Obsidian Note Format

Klemma creates and reads `@citekey.md` notes with YAML frontmatter:

```yaml
---
citekey: "smithMachineLearning2020"
title: "Machine Learning for NLP..."
author: "John Smith..."
year: 2020
quality: 5
priority: "high"
chapter: 2
section: "2.3.1"
sections: [1.4.3, 2.2.2, 2.3.1]
chapters: [1, 2, 3]
tags: ["NLP", "Machine-Learning"]
---
```

`chapter`/`section` — primary assignment. `sections`/`chapters` — all relevant. `klemma process` creates notes automatically.

**Where reports are saved**: AI reports (`outline`, `research`, `library`) are saved to the project root (`project_root/`). Only `@citekey.md` notes go to the vault (`notes_folder`).

## Architecture

```
klemma (CLI, v0.4.1)
├── AI Provider ─────── AI analysis (pluggable backend)
│   ├── LiteLLMClient ─ 100+ providers (litellm SDK) — recommended, default
│   ├── ClaudeClient ── Claude Code CLI (claude -p)
│   └── OpenAIClient ── deprecated (delegates to LiteLLM)
├── Error Taxonomy ──── KlemmaAIError (timeout/rate-limit/auth/response)
│   └── AICallResult ── timing, tokens, retries, model metadata
├── Embeddings ──────── semantic search (pluggable backend)
│   ├── SemanticScholar ─ S2 API (768-dim SPECTER, free)
│   ├── LocalSPECTER ──── sentence-transformers (offline)
│   └── OpenAI ─────────── text-embedding-3-small (1536-dim)
├── Fragment RAG ────── semantic fragment search (ask, research)
├── LibraryProvider ── BBT JSON → citekey/PDF/metadata
├── Obsidian vault ─── @citekey.md + research notes + reports
├── BetterBibTeX JSON ─ citekey → PDF path mapping
├── Zotero storage ─── PDF files
├── PyMuPDF ────────── PDF text extraction
├── Config ────────── ~/.klemmarc.yaml → ~/.klemma/ → .klemma/ (3-level merge)
└── SQLite (schema v5)
    ├── sources ─────────── Zotero entries (+ embedding BLOB, embedding_model)
    ├── source_sections ─── source × section (multi-section)
    ├── fragments ───────── citation fragments (+ citation_intent, embedding, embedding_model)
    ├── reference_gaps ──── bibliography gaps (+ citation_intent, intent scoring)
    ├── citation_links ──── citation graph (source → target, intent, in_library)
    ├── daily_plans ─────── generated plans
    ├── reading_queue ───── prioritized reading list
    ├── prune_verdicts ──── audit results (drop/maybe)
    └── benchmark_runs ──── benchmark history (metrics, config_snapshot, git_commit)
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
ruff check src/ tests/
```

See [CLAUDE.md](CLAUDE.md) for detailed architecture docs, module descriptions, and data flows.

## License

Klemma is a free tool for researchers, but **not for commercial use**.

- **Core** (`src/klemma/`) — [Polyform Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/). Free for academic, research, and personal use. Commercial use is prohibited.
- **SaaS** (`saas/`) — proprietary, all rights reserved.

For commercial licensing: ilya.bolkhovsky@gmail.com
