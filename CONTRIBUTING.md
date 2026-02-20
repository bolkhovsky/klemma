# Contributing to Klemma

Thanks for your interest! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/bolkhovsky/klemma.git
cd klemma
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check src/ tests/
```

CI runs both `ruff check` and `pytest` on every PR.

## Prompt Language Convention

Shipped prompts in `prompts/` must be in **English** with no hardcoded language strings. Every prompt that produces AI output should end with:

```
Respond in {{ language }}.
```

The `ai.language` config field controls the response language at runtime. Users can override any prompt by placing a custom version in `~/.klemma/prompts/`.

## Pull Requests

1. Fork the repo and create a feature branch from `master`
2. Make your changes, add tests if applicable
3. Ensure `ruff check` and `pytest` pass
4. Open a PR with a clear description of what changed and why
