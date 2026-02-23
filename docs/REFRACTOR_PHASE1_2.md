# Refactor Phase 1-2 Baseline and Execution Notes

## Scope

This document tracks the active refactoring kickoff:

- Phase 1: scope + no-regression baseline
- Phase 2: security hardening first

## Phase 1 Baseline (no-regression guardrails)

Core workflows to keep stable during refactor:

1. `klemma init`
2. `klemma process`
3. `klemma research`
4. `klemma library`
5. `klemma ask`

Quality gate snapshot before Phase 2 implementation:

- `pytest -q`: `207 passed`
- `ruff check src tests`: `All checks passed`

No-regression checklist for each refactor PR:

1. Core workflows still execute without CLI signature/flag breakage.
2. `pytest -q` passes.
3. `ruff check src tests` passes.
4. Security tests stay green.

## Phase 2 Security Changes

### 1. Vault path boundary enforcement

File: `src/klemma/vault.py`

- All write/list folder targets are resolved and validated under `vault_path`.
- Traversal attempts (for `folder` and `name`) now raise `ValueError`.

### 2. Acquire URL and download hardening

File: `src/klemma/skills/acquirer.py`

- URL allow policy: only external `http`/`https`.
- Block localhost/private/link-local/reserved IP targets.
- Enforce max download size (`MAX_DOWNLOAD_BYTES = 50MB` default).
- Abort and clean up temp file if streamed payload exceeds limit.

### 3. Security regression tests

File: `tests/test_security_hardening.py`

- Vault traversal rejection tests.
- Unsafe URL blocking tests.
- Content-length and streaming size-limit tests.

