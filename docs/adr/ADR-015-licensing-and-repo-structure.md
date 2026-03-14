# ADR-015: Licensing and Repository Structure

- **Status**: Accepted
- **Date**: 2026-03-14
- **Depends on**: ADR-009 (SaaS Architecture)
- **Supersedes**: None

## Context

Klemma is transitioning from a CLI-only tool to an open-source project with a commercial SaaS layer. Two architectural decisions need to be locked down before SaaS code enters the codebase:

1. **Licensing model** — what can users and companies do with Klemma?
2. **Repository structure** — where does SaaS code live relative to the open-source core?

### Constraints

- **Mission**: Klemma exists to give researchers a tool that unlocks new possibilities (like Sci-Hub unlocked access). Commercial exploitation of the core is antithetical to this mission.
- **Solo developer**: Cross-repo coordination is an outsized tax on a solo operator with ~6 productive hours/day.
- **Speed > process**: Iteration speed matters more than architectural purity at this stage.
- **SaaS protection**: The hosted service must be protected from competitors cloning and hosting it.

## Decisions

### 1. Core License: Polyform Noncommercial 1.0.0

**Decision**: License all code under `src/klemma/` (the open-source core) under the [Polyform Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).

**Rationale**:
- Explicitly prohibits commercial use — no ambiguity, no reliance on copyleft economics
- Free for academic, research, personal, and educational use — exactly Klemma's target audience
- Well-drafted by experienced IP lawyers (unlike CC-BY-NC which wasn't designed for software)
- Allows modification and redistribution for noncommercial purposes
- Dual licensing is still possible: companies wanting commercial use can negotiate a separate license

**Trade-offs accepted**:
- Not OSI-approved — some institutions/grants may require OSI licenses. Mitigated by the fact that most academic users don't check license type if the tool is free for research.
- "Noncommercial" edge cases (commercially-funded research labs, industry PhD students) — Polyform NC defines commercial use as "use primarily for a commercial advantage or monetary compensation." Academic research at a funded institution is clearly noncommercial.

**What changes**:
- Replace `LICENSE` (currently MIT) with Polyform Noncommercial 1.0.0 text
- Update `pyproject.toml`: `license = {text = "Polyform-Noncommercial-1.0.0"}`
- Remove `"License :: OSI Approved :: MIT License"` classifier
- Add license header to source files (optional, defer)

### 2. SaaS License: Proprietary (All Rights Reserved)

**Decision**: All code under `saas/` is proprietary. No open-source license, no source-available license.

**Rationale**:
- Simplest possible approach — no license ambiguity
- Prevents competing SaaS, prevents self-hosting of the commercial layer
- No need for BSL-1.1 or similar "source-available" complexity — the SaaS directory is not meant to be reused
- Source code is visible in the repo (monorepo) but not licensed for any use

**`saas/LICENSE`**:
```
Copyright (c) 2026 Ilya Bolkhovskiy. All rights reserved.

This code is proprietary. No permission is granted to use, copy, modify,
distribute, or create derivative works from this code without explicit
written permission from the copyright holder.
```

### 3. Repository Structure: Monorepo with Directory Separation

**Decision**: Keep all code in a single repository (`klemma-ai/klemma`). SaaS-specific code lives in a `saas/` top-level directory with its own LICENSE.

**Structure**:
```
klemma/
├── src/klemma/           # Polyform Noncommercial — open core
│   ├── skills/           # existing domain logic
│   ├── auth/             # authentication module (shared)
│   ├── api/              # FastAPI skeleton (shared)
│   ├── commands/         # CLI commands
│   └── ...
├── saas/                 # Proprietary — commercial SaaS layer
│   ├── LICENSE           # All rights reserved
│   ├── billing/          # Stripe/payment integration
│   ├── dashboard/        # Web frontend
│   ├── middleware/        # Rate limiting, usage metering, multi-tenancy
│   ├── deploy/           # Docker Compose, nginx configs
│   └── tests/
├── tests/                # Core tests (Polyform NC)
├── LICENSE               # Polyform Noncommercial 1.0.0
└── pyproject.toml
```

**Rationale**:
- **Solo tax**: Cross-repo coordination costs 10-30 min per change. With ~6 productive hours/day, this is a meaningful tax. GitLab ran CE+EE in one repo with hundreds of engineers because even they found the coordination cost too high.
- **Refactoring**: Core API changes and SaaS consumer updates happen in the same commit. No version drift.
- **One CI pipeline**: Single GitHub Actions config, single test suite, single release process.
- **Splitting is easy, merging is hard**: If Klemma grows a team and needs separate repos, `git filter-branch` or `git subtree split` extracts `saas/` in an afternoon. Going the other direction is a nightmare.

**Boundary rule**: `src/klemma/` NEVER imports from `saas/`. The dependency is strictly one-way: `saas/` imports from `src/klemma/`.

**Examples of this pattern**: GitLab (CE+EE), Cal.com, PostHog, Sentry.

## Alternatives Considered

### Separate repositories
Rejected. Too expensive for a solo developer. Every cross-cutting change (core API refactor, schema migration, shared auth) requires coordinated PRs in two repos. Version sync is a constant overhead.

### AGPL-3.0 for core
Rejected. The user explicitly requires prohibition of commercial use, not deterrence via copyleft. AGPL technically allows commercial use — it just makes it expensive. Polyform NC is an explicit, unambiguous "no."

### BSL-1.1 for SaaS
Rejected. BSL converts to open source after N years and signals openness. The SaaS layer is not meant to be open — simple proprietary is clearer and simpler.

### Feature flags (single directory, gated features)
Rejected. Tight coupling between free and paid features. License checks scattered through code. Harder to reason about what's open vs. commercial. Directory separation is cleaner.

## Consequences

- **Researchers** get a free, modifiable tool with clear noncommercial terms
- **Companies** wanting commercial use must negotiate a separate license (potential revenue stream)
- **Competitors** cannot clone and host Klemma SaaS
- **Contributors** submit to core under Polyform NC terms (may need CLA for dual-licensing in future)
- **pyproject.toml classifier** changes from OSI-approved to custom license text
- **README** should explain the dual-license structure clearly

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Some institutions require OSI licenses | MEDIUM | Most academic users don't check; tool is free for research |
| "Noncommercial" definition disputes | LOW | Polyform NC has clear definition; academic research is unambiguously noncommercial |
| Contributors reluctant without OSI license | LOW | Solo project, not relying on community contributions yet |
| Dual-licensing needs CLA | LOW | Add CLA when/if external contributions start coming |

## References

- [Polyform Noncommercial 1.0.0 full text](https://polyformproject.org/licenses/noncommercial/1.0.0/)
- ADR-009: SaaS Architecture
- ADR-014: Three-tier library split (defines Protocol boundary between CLI and SaaS backends)
