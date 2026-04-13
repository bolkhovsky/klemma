# ADR-016: Default-local notes and raw PDF sidecars

- **Status**: Accepted
- **Date**: 2026-04-11
- **Depends on**: ADR-014 (Three-tier library split)
- **Supersedes**: None

## Context

Two independent but intertwined frictions had accumulated around how klemma stores data on disk:

1. **Obsidian was mandatory at init time.** The `klemma init` wizard asked every new user for a vault path, notes folder, and tags folder — and hard-failed at `klemma process` when those were missing. In practice, 90%+ of the people klemma serves (PhD students, early-career researchers, downstream tool users) don't have an Obsidian vault and have no reason to set one up just to try the tool. Existing power users who *do* use Obsidian are a valuable but small minority.
2. **Raw PDF text had no on-disk trace.** At `klemma process` time the full PDF text was extracted into memory, fed to the AI for fragment extraction, and discarded. There was no human-readable artifact to grep, diff, or debug. The upcoming semantic citation drift checker (planned, separate PR) needs a cheap local source of primary-source passages — not a `fitz`-re-open on every check and not a `WebFetch` to the original URL.

At the same time, klemma's competitor-adjacent tool feynman's `/lit` dump format (flat prose + `<!-- Page N -->` markers + a small frontmatter block) had demonstrated itself as a surprisingly effective format for grep-level workflows. It is trivial to produce, trivial to consume, and pairs cleanly with klemma's existing `.klemma/` project-scoped storage.

## Decisions

### 1. Default-local layout for annotated notes

**Decision**: Fresh projects write `@<citekey>.md` to `<project_root>/.klemma/notes/` by default. Obsidian integration is an opt-in override.

**How**: `klemma init` no longer prompts for `vault_path`, `notes_folder`, or `tags_folder`. It pre-creates `.klemma/notes/` and `.klemma/pdfs/`. The fresh `config.yaml` has no `obsidian:` section. Existing projects with `obsidian.vault_path` set continue to work unchanged — the override kicks in whenever the user has written it to `config.yaml` by hand.

**Override**: add to `.klemma/config.yaml`:
```yaml
obsidian:
  vault_path: "/path/to/vault"
  notes_folder: "References"
```

### 2. Raw PDF sidecar at `<project_root>/.klemma/pdfs/<citekey>.md`

**Decision**: On every successful PDF extraction, klemma writes a raw-text sidecar in feynman-style format: YAML-like frontmatter + `---` + flat prose + `<!-- Page N -->` markers between pages.

**Rationale**:
- **Debuggability**: users can see exactly what text the AI received when a fragment looks wrong
- **Provenance**: the sidecar is the canonical on-disk trace of "what was in the PDF when we processed it" — cheaper than re-opening with `fitz` every time and more stable than the source PDF (which can be moved/renamed)
- **Downstream consumer**: the semantic citation drift checker (planned) will grep these sidecars as its primary-source passage store; it is the second consumer after `write_pdf_sidecar()` itself

### 3. The raw sidecar is *always* local

**Decision**: The Obsidian override applies only to annotated `@<citekey>.md` notes. Raw dumps always land in `<project_root>/.klemma/pdfs/`, regardless of whether a vault is configured.

**Rationale**: The raw dump is a project-level debugging artifact, not content read in Obsidian. Mixing gigabytes of raw PDF text into someone's Obsidian vault would degrade their vault experience for zero gain. Keeping it project-local also means reproducibility (you can tar up a project and its raw traces go with it) and downstream tools can hardcode the path.

### 4. `VaultAdapter` re-rooted at the notes directory

**Decision**: `VaultAdapter` is now constructed with the *resolved notes directory* as its root, not the vault root. A module-level helper `resolve_notes_root(config, project_root) -> Path` is the single source of truth for where annotated notes land.

**Rationale**: Before this change, every call site re-appended `cfg.obsidian.notes_folder` to a vault-root adapter via `folder=...` args. In local mode there *is* no vault root, so either every call site would need a fallback or we re-root the adapter. Re-rooting is smaller and kills the folder arg from every call site. `_resolve_folder(None)` returns `self.vault_path` which is exactly what we want.

**Resolver logic**:
- `config.obsidian.vault_path` set (non-whitespace) → `Path(vault_path).expanduser() / notes_folder` (joined path; `notes_folder=""` yields the vault root, preserving the flat-vault edge case)
- Otherwise → `project_root / ".klemma" / "notes"`

**Known semantic narrowing**: `vault.get_tags()`, `vault.search()`, and `vault._find_daily_dir()` used to scan the whole vault. They now scan only the notes directory. This matches what those helpers are *actually used for* in klemma (citekey-note operations and tag inventory of `@<citekey>.md` files), but it is a behavior change worth flagging in release notes for existing Obsidian users.

### 5. Three format contracts for downstream consumers

The raw PDF sidecar is infrastructure for the planned semantic citation drift checker (and any future tool that wants to read processed PDF text). This ADR locks in three format contracts that must not drift without a version bump:

**Contract 1 — Sidecar path.** The sidecar for `<citekey>` in project `<root>` is always at `<root>/.klemma/pdfs/<citekey>.md`. No config override. Downstream consumers can hardcode this path.

**Contract 2 — Page delimiter.** The delimiter between pages is exactly `\n<!-- Page N -->\n` where `N = 2, 3, ...` (page 1 has no marker — it starts immediately after the frontmatter `---`). The regex `\n<!-- Page (\d+) -->\n` is a stable split point.

**Contract 3 — Frontmatter field set.** The stable fields are `Citekey`, `Authors`, `Year`, `DOI`, `Pages`, `Source`. Additions are allowed (append-only). Renames and removals require a version bump note in the sidecar header and a migration path.

`PDFExtractor.extract_pages()` is also part of the public API — signature `(pdf_path: Path) -> list[str]`, one cleaned string per page, no truncation. The semantic citation checker is the second consumer of this method, so the signature is load-bearing.

## Consequences

### Positive

- **Onboarding friction**: zero Obsidian questions in the wizard; zero "you must set vault_path" hard-failures at `process` time
- **Debuggability**: every processed PDF leaves a grep-able trace
- **Downstream infrastructure**: the semantic citation checker has a stable, project-local, cheap fetch source
- **No config migration**: existing Obsidian projects keep working because the resolver feeds their old layout into the adapter unchanged
- **`migrate-frontmatter` and `import --with-queue` become mode-agnostic**: they used to hard-fail when `vault is None`, now they always have a valid `kctx.vault`

### Negative

- **`get_tags()` / `search()` / `_find_daily_dir()` scope narrowing** (see Decision 4). Documented in the release note for existing Obsidian users.
- **Disk usage**: raw PDF sidecars add text-file-scale overhead per processed source (~50-500KB per paper). Projects with hundreds of sources will see tens of MB in `.klemma/pdfs/`. Acceptable — text compresses well if the user wants to clean up, and the files are never loaded into memory all at once.
- **Citekey validation surface**: `write_pdf_sidecar()` must reject `..`, `/`, `\`, and empty citekeys. Pattern mirrors `LocalFileStore._file_path()`.

## Alternatives considered

- **Rip out `ObsidianConfig` entirely.** Rejected — power users who already have working Obsidian integrations should not be forced to migrate, and keeping the override path is nearly free.
- **Config override for `pdfs_dir`.** Rejected — hardcoding `.klemma/pdfs/` makes downstream consumers simpler and avoids a "where is my raw text" support surface.
- **Page-number tagging on extracted fragments.** Deferred — requires threading page info through `skills/extractor.py` and the extraction prompt. Tracked under issue #299 (provenance sidecars).
- **Splitting `VaultAdapter` into `NotesAdapter` + `VaultAdapter`.** Rejected as premature. The single-adapter-reroot approach is smaller and sufficient. Revisit if a future feature needs both vault-wide and notes-scoped operations simultaneously.

## Implementation notes

- `src/klemma/literature/pdf.py` — `PDFExtractor.extract_pages()` reuses `fitz.open()` + `_clean_text()`, returns unbounded `list[str]`
- `src/klemma/literature/sidecar.py` — new module; `write_pdf_sidecar()` uses the atomic-write pattern from `save_klemma_md()` (tempfile + `os.replace` + try/except unlink)
- `src/klemma/vault.py` — `resolve_notes_root()` is module-level; every `VaultAdapter` construction goes through it
- `src/klemma/setup.py` — `init_project()` creates both `.klemma/notes/` and `.klemma/pdfs/`; `_build_project_config()` skips the `obsidian:` section when `values.vault_path` is empty
- `config.project.example.yaml` / `config.example.yaml` — the `obsidian:` and `zotero:` blocks are commented out, not live defaults

See also:
- `src/klemma/literature/CLAUDE.md` — documents the three format contracts
- `src/klemma/CLAUDE.md` — documents `resolve_notes_root()` and the narrowed `get_tags()`/`search()` scope
- `docs/USER_GUIDE.md § 2.8` — user-facing storage documentation
