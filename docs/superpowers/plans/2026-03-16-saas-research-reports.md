# SaaS Research Reports Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `klemma research -s` into the SaaS backend — generate per-section research reports with DB persistence and display in the Research view.

**Architecture:** Create `_SaaSStateAdapter` that wraps three-tier stores (paper_store + project_store + user_library) to satisfy the `StateManager` interface required by `research_section()`. Add `_NullVault` stub. Persist reports in `research_reports` table in `users.db`. New REST endpoint for reading stored reports. Update frontend ResearchView to show report content.

**Tech Stack:** Python (FastAPI, RQ, SQLite), Vue 3 + TypeScript, existing `skills/researcher.py`

**Issue:** #205

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/klemma/api/adapters.py` | Create | `_SaaSStateAdapter` + `_NullVault` — adapter classes for headless mode |
| `src/klemma/stores/user_store.py` | Modify | Add `research_reports` table (schema v6), CRUD methods |
| `src/klemma/api/tasks.py` | Modify | Wire `generate_research()` to real `research_section()` call |
| `src/klemma/api/routes/write.py` | Modify | Pass `project_id` + `user_id` to task |
| `src/klemma/api/routes/projects.py` | Modify | Add `GET /{project_id}/research/{section}` endpoint |
| `saas/dashboard/src/api/client.ts` | Modify | Add research report API methods |
| `saas/dashboard/src/views/ResearchView.vue` | Modify | Show stored reports, generation with project context |

---

### Task 1: _NullVault and _SaaSStateAdapter

**Files:**
- Create: `src/klemma/api/adapters.py`

- [ ] **Step 1: Create adapters.py with _NullVault**

`_NullVault` implements the subset of `VaultAdapter` that `research_section()` and `load_section_sources()` call: `read_note()`, `get_properties()`, `list_notes()`. All return safe defaults.

```python
"""Adapter classes for headless SaaS mode (no Obsidian vault, no monolithic StateManager)."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class _NullVault:
    """Stub VaultAdapter for SaaS — no Obsidian vault available.

    Returns empty/safe defaults for all reads. Satisfies the VaultAdapter
    interface expected by research_section() and context_loader functions.
    """

    vault_path = ""

    def read_note(self, name: str) -> str:
        return ""

    def get_properties(self, name: str) -> dict:
        return {}

    def list_notes(self, folder: str = "", pattern: str = "*.md") -> list[str]:
        return []

    def write_note(self, name: str, content: str, folder: str = "") -> None:
        pass

    def update_section(self, name: str, heading: str, content: str) -> None:
        pass

    def check_folder(self, folder: str) -> bool:
        return False

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return []
```

- [ ] **Step 2: Add _SaaSStateAdapter to adapters.py**

This wraps `paper_store` + `project_store` + `user_library` to satisfy the `StateManager` method calls in `research_section()`. Only implements the methods actually called by the researcher skill.

The key methods called by `research_section()` are:
- `get_by_section(section)` → list of source dicts with `id`, `title`, `authors`, etc.
- `get_by_chapter(chapter)` → same shape
- `get_source(citekey)` → source dict or None
- `get_fragments(section=..., source_id=..., chapter=..., limit=...)` → list of fragment dicts
- `get_coverage_stats()` → dict with sections/chapters/totals
- `get_gaps(min_sources=...)` → list of gap dicts (return empty for MVP)
- `get_fragment_stats()` → dict with counts
- `get_existing_source_ids()` → set of citekey strings
- `get_all_sources()` → list of source dicts
- `retrieve_similar_fragments(...)` → list (return empty — RAG deferred)

```python
class _SaaSStateAdapter:
    """Wraps three-tier stores to satisfy StateManager interface for skills.

    research_section() calls ~10 methods on its `state` parameter.
    This adapter translates those calls to paper_store + project_store + user_library
    queries. Only methods actually used by researcher.py are implemented.

    NOT a full StateManager replacement — intentionally minimal.
    """

    def __init__(self, paper_store, project_store, user_library):
        self._paper = paper_store
        self._project = project_store
        self._library = user_library

    def get_by_section(self, section: str, section_type: str | None = None) -> list[dict]:
        """Sources assigned to a section — from project_store + paper_store metadata."""
        citekeys = self._project.get_sources_by_section(section)
        return self._enrich_sources(citekeys)

    def get_by_chapter(self, chapter: int) -> list[dict]:
        """Sources assigned to any section in a chapter."""
        stats = self._project.get_coverage_stats()
        chapter_prefix = f"{chapter}."
        citekeys = set()
        for sec in stats.get("sections", {}):
            if sec == str(chapter) or sec.startswith(chapter_prefix):
                citekeys.update(self._project.get_sources_by_section(sec))
        return self._enrich_sources(list(citekeys))

    def get_source(self, source_id: str) -> dict | None:
        """Source metadata by citekey."""
        src = self._library.get_source_by_citekey(source_id)
        if not src:
            return None
        paper = self._paper.get_paper_by_id(src.paper_id)
        if not paper:
            return None
        frags = self._paper.get_fragments(src.paper_id)
        return {
            "id": source_id,
            "title": paper.title or "",
            "authors": paper.authors or "",
            "year": paper.year,
            "doi": paper.doi or "",
            "abstract": paper.abstract or "",
            "fragment_count": len(frags),
            "quality_score": 0,
            "primary_chapter": None,
            "primary_section": None,
            "relevance_nr1": 0,
            "relevance_nr2": 0,
            "citation_priority": "medium",
        }

    def get_fragments(
        self,
        source_id: str | None = None,
        chapter: int | None = None,
        section: str | None = None,
        fragment_type: str | None = None,
        limit: int = 50,
        section_type: str | None = None,
    ) -> list[dict]:
        """Fragments from paper_store, filtered by section assignments from project_store."""
        if source_id:
            src = self._library.get_source_by_citekey(source_id)
            if not src:
                return []
            frags = self._paper.get_fragments(src.paper_id)
            return [self._frag_to_dict(f, source_id) for f in frags[:limit]]

        # Section/chapter-based: get all citekeys for section, then their fragments
        citekeys = []
        if section:
            citekeys = self._project.get_sources_by_section(section)
        elif chapter:
            stats = self._project.get_coverage_stats()
            prefix = f"{chapter}."
            for sec in stats.get("sections", {}):
                if sec == str(chapter) or sec.startswith(prefix):
                    citekeys.extend(self._project.get_sources_by_section(sec))
            citekeys = list(set(citekeys))

        result = []
        for ck in citekeys:
            src = self._library.get_source_by_citekey(ck)
            if not src:
                continue
            frags = self._paper.get_fragments(src.paper_id)
            for f in frags:
                result.append(self._frag_to_dict(f, ck))
                if len(result) >= limit:
                    return result
        return result

    def get_coverage_stats(self) -> dict:
        return self._project.get_coverage_stats()

    def get_gaps(self, min_sources: int = 3) -> list[dict]:
        return []  # project_store stub returns empty

    def get_fragment_stats(self) -> dict:
        """Compute basic fragment stats from paper_store."""
        all_sources = self._library.get_all_sources()
        total = 0
        by_type: dict[str, int] = {}
        for src in all_sources:
            frags = self._paper.get_fragments(src.paper_id)
            total += len(frags)
            for f in frags:
                ft = f.fragment_type or "key_idea"
                by_type[ft] = by_type.get(ft, 0) + 1
        return {"total": total, "by_type": by_type}

    def get_existing_source_ids(self) -> set[str]:
        return self._library.get_existing_citekeys()

    def get_all_sources(self) -> list[dict]:
        all_src = self._library.get_all_sources()
        return self._enrich_sources([s.citekey for s in all_src])

    def retrieve_similar_fragments(
        self, query_embedding, top_k: int = 10, model: str | None = None
    ) -> list[dict]:
        return []  # RAG deferred

    # -- internal helpers --

    def _enrich_sources(self, citekeys: list[str]) -> list[dict]:
        """Convert citekey list to source dicts with paper metadata."""
        result = []
        for ck in citekeys:
            src = self.get_source(ck)
            if src:
                result.append(src)
        return result

    @staticmethod
    def _frag_to_dict(f, citekey: str) -> dict:
        """Convert FragmentRecord to dict matching StateManager fragment format."""
        return {
            "id": f.fragment_id,
            "source_id": citekey,
            "citekey": citekey,
            "fragment_text": f.fragment_text,
            "fragment_type": f.fragment_type or "key_idea",
            "page_number": f.page_number,
            "citation_intent": f.citation_intent,
            "section": "",
            "relevance_score": 3,
            "usage_hint": "",
        }
```

- [ ] **Step 3: Verify with ruff**

Run: `cd /Users/ilya/projects/klemma && python -m ruff check src/klemma/api/adapters.py`

- [ ] **Step 4: Commit**

```bash
git add src/klemma/api/adapters.py
git commit -m "feat: add _SaaSStateAdapter + _NullVault for headless research (#205)"
```

---

### Task 2: DB schema — research_reports table

**Files:**
- Modify: `src/klemma/stores/user_store.py`

- [ ] **Step 1: Add migration for schema v6**

In `_migrate_schema()`, add after the `if version < 5:` block:

```python
if version < 6:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS research_reports (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id    TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            section       TEXT NOT NULL,
            report_json   TEXT NOT NULL,
            report_text   TEXT NOT NULL,
            model         TEXT,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, section)
        );
        CREATE INDEX IF NOT EXISTS idx_rr_project ON research_reports(project_id);
    """)
```

Bump `_SCHEMA_VERSION = 6`.

- [ ] **Step 2: Add save_research_report method**

```python
def save_research_report(
    self,
    project_id: str,
    section: str,
    report_json: str,
    report_text: str,
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Save or replace a research report for a project section."""
    with self._conn() as conn:
        conn.execute(
            """INSERT INTO research_reports
               (project_id, section, report_json, report_text, model, input_tokens, output_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, section) DO UPDATE SET
                 report_json = excluded.report_json,
                 report_text = excluded.report_text,
                 model = excluded.model,
                 input_tokens = excluded.input_tokens,
                 output_tokens = excluded.output_tokens,
                 created_at = datetime('now')""",
            (project_id, section, report_json, report_text, model, input_tokens, output_tokens),
        )
```

- [ ] **Step 3: Add get_research_report method**

```python
def get_research_report(self, project_id: str, section: str) -> dict | None:
    """Get the latest research report for a project section."""
    with self._conn() as conn:
        row = conn.execute(
            "SELECT * FROM research_reports WHERE project_id = ? AND section = ?",
            (project_id, section),
        ).fetchone()
    if not row:
        return None
    return dict(row)

def get_project_research_reports(self, project_id: str) -> list[dict]:
    """Get all research reports for a project, ordered by section."""
    with self._conn() as conn:
        rows = conn.execute(
            "SELECT section, created_at, model, input_tokens, output_tokens FROM research_reports WHERE project_id = ? ORDER BY section",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Verify with ruff + tests**

Run: `cd /Users/ilya/projects/klemma && python -m ruff check src/klemma/stores/user_store.py && python -m pytest tests/ -q --tb=short -x`

- [ ] **Step 5: Commit**

```bash
git add src/klemma/stores/user_store.py
git commit -m "feat: research_reports table in users.db (schema v6) (#205)"
```

---

### Task 3: Wire generate_research task

**Files:**
- Modify: `src/klemma/api/tasks.py`

- [ ] **Step 1: Replace generate_research stub with real implementation**

Replace the existing `generate_research()` function:

```python
def generate_research(section: str, project_id: str, data_dir: str, user_id: str = "") -> dict:
    """Generate a research briefing for a section using researcher.py.

    Headless mode: no vault, no RAG, no incremental.
    Persists result in research_reports table.
    """
    from klemma.api.adapters import _NullVault, _SaaSStateAdapter
    from klemma.config import KlemmaConfig
    from klemma.stores.file_store import LocalFileStore
    from klemma.stores.paper_store import LocalPaperStore
    from klemma.stores.project_store import LocalProjectStore
    from klemma.stores.user_library import LocalUserLibrary
    from klemma.stores.user_store import LocalUserStore

    data_path = Path(data_dir)
    library_db = data_path / "library.db"
    paper_store = LocalPaperStore(library_db)
    user_library = LocalUserLibrary(library_db)
    project_store = LocalProjectStore(data_path / "project.db")
    user_store = LocalUserStore(data_path / "users.db")

    # Check token limit
    if user_id and not user_store.check_token_limit(user_id):
        return {"status": "error", "detail": "Token limit exhausted"}

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return {"status": "error", "detail": "No AI API key configured"}

    # Load project outline for dissertation_context
    dissertation_context = ""
    if project_id:
        project = user_store.get_project_by_id(project_id)
        if project and project.get("outline"):
            outline = project["outline"]
            dissertation_context = "Dissertation sections:\n" + "\n".join(
                f"  {s['id']}: {s['name']}" for s in outline
            )

    try:
        ai, ai_config = _create_ai_provider()

        # Build adapter
        state_adapter = _SaaSStateAdapter(paper_store, project_store, user_library)
        vault = _NullVault()

        # Minimal config
        config = KlemmaConfig()

        from klemma.skills.researcher import research_section

        result = research_section(
            section=section,
            config=config,
            state=state_adapter,
            vault=vault,
            ai=ai,
            save_to_vault=False,
            project=None,
            dissertation_context=dissertation_context,
            klemma_home=None,
            project_root=None,
            embeddings=None,
            paper_store=paper_store,
            user_library=user_library,
        )

        # Record token usage (approximate — research_section uses call_json internally)
        # We'll estimate from the result text length
        if user_id:
            user_store.record_usage(
                user_id=user_id,
                operation="generate_research",
                model=ai_config.model,
                input_tokens=0,  # call_json doesn't expose tokens
                output_tokens=0,
                section=section,
            )

        # Persist report
        import json
        report_json = json.dumps(result.model_dump(), ensure_ascii=False, default=str)
        user_store.save_research_report(
            project_id=project_id,
            section=section,
            report_json=report_json,
            report_text=result.research_text,
            model=ai_config.model,
        )

        logger.info("Research report generated for section %s (project %s)", section, project_id)

        return {
            "status": "completed",
            "section": section,
            "report_text": result.research_text,
        }

    except Exception as exc:
        logger.error("Research generation failed for %s: %s", section, exc, exc_info=True)
        return {"status": "error", "detail": f"Research failed: {type(exc).__name__}: {exc}"}
```

- [ ] **Step 2: Verify with ruff**

Run: `cd /Users/ilya/projects/klemma && python -m ruff check src/klemma/api/tasks.py`

- [ ] **Step 3: Commit**

```bash
git add src/klemma/api/tasks.py
git commit -m "feat: wire generate_research to researcher.py via SaaS adapters (#205)"
```

---

### Task 4: Update write endpoint + add GET report endpoint

**Files:**
- Modify: `src/klemma/api/routes/write.py`
- Modify: `src/klemma/api/routes/projects.py`

- [ ] **Step 1: Update write.py to pass project_id and user_id**

Update `WriteJobRequest` to include optional `project_id`:

```python
class WriteJobRequest(BaseModel):
    section: str
    project_id: str | None = None
```

Update `submit_research_job` to pass user + project:

```python
@router.post("/research", response_model=WriteJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_research_job(
    body: WriteJobRequest,
    user: UserRecord = Depends(get_current_user),
) -> WriteJobResponse:
    return _enqueue_write_task("generate_research", body.section, body.project_id, user.user_id)
```

Update `_enqueue_write_task` signature and enqueue call:

```python
def _enqueue_write_task(task_name: str, section: str, project_id: str | None = None, user_id: str = "") -> WriteJobResponse:
    ...
    if task_name == "generate_research":
        job = q.enqueue(task_fn, section, project_id or "", data_dir, user_id, job_timeout=600)
    else:
        job = q.enqueue(task_fn, section, data_dir, job_timeout=600)
    ...
```

- [ ] **Step 2: Add GET endpoint to projects.py**

```python
@router.get("/{project_id}/research/{section}")
async def get_research_report(
    project_id: str,
    section: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Get the stored research report for a project section."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    report = store.get_research_report(project_id, section)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report for this section")

    return {
        "section": report["section"],
        "report_text": report["report_text"],
        "model": report["model"],
        "created_at": report["created_at"],
    }


@router.get("/{project_id}/research")
async def list_research_reports(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """List all research reports for a project."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    reports = store.get_project_research_reports(project_id)
    return {"project_id": project_id, "reports": reports}
```

- [ ] **Step 3: Verify with ruff + tests**

Run: `cd /Users/ilya/projects/klemma && python -m ruff check src/klemma/api/routes/write.py src/klemma/api/routes/projects.py && python -m pytest tests/ -q --tb=short -x`

- [ ] **Step 4: Commit**

```bash
git add src/klemma/api/routes/write.py src/klemma/api/routes/projects.py
git commit -m "feat: research report endpoints — POST with project_id, GET stored reports (#205)"
```

---

### Task 5: Frontend — API client + ResearchView

**Files:**
- Modify: `saas/dashboard/src/api/client.ts`
- Modify: `saas/dashboard/src/views/ResearchView.vue`

- [ ] **Step 1: Update API client**

Add to `client.ts`:

```typescript
// In the `research` export:
export const research = {
  generate: (section: string, projectId?: string) =>
    request<{ job_id: string; status: string; section: string; task_type: string }>('/write/research', {
      method: 'POST',
      body: JSON.stringify({ section, project_id: projectId }),
    }),

  getReport: (projectId: string, section: string) =>
    request<{ section: string; report_text: string; model: string; created_at: string }>(
      `/projects/${projectId}/research/${encodeURIComponent(section)}`
    ),

  listReports: (projectId: string) =>
    request<{ project_id: string; reports: { section: string; created_at: string }[] }>(
      `/projects/${projectId}/research`
    ),
}
```

- [ ] **Step 2: Update ResearchView.vue**

Key changes:
- On mount, call `research.listReports()` to know which sections have reports
- On section expand, load stored report via `research.getReport()`
- Show report markdown above source list in expanded section
- "Сгенерировать обзор" passes `projectId` to `research.generate()`
- Show "Обновить" button for sections that already have a report
- Show report `created_at` timestamp

- [ ] **Step 3: Build frontend**

Run: `cd /Users/ilya/projects/klemma/saas/dashboard && npm run build`

- [ ] **Step 4: Commit**

```bash
git add saas/dashboard/src/api/client.ts saas/dashboard/src/views/ResearchView.vue
git commit -m "feat: research report display in ResearchView (#205)"
```

---

### Task 6: Deploy + verify

- [ ] **Step 1: Run ruff + tests**

```bash
cd /Users/ilya/projects/klemma
python -m ruff check src/ tests/
python -m pytest tests/ -q --tb=short -x
```

- [ ] **Step 2: Deploy backend**

Follow klemma-deploy skill: scp changed files → docker cp → restart api + worker.

Files to deploy:
- `src/klemma/api/adapters.py` (new)
- `src/klemma/api/tasks.py` (modified)
- `src/klemma/api/routes/write.py` (modified)
- `src/klemma/api/routes/projects.py` (modified)
- `src/klemma/stores/user_store.py` (modified)

- [ ] **Step 3: Deploy frontend**

```bash
cd /Users/ilya/projects/klemma/saas/dashboard
npm run build
scp -r dist/* klemma:/opt/klemma/dashboard/
```

- [ ] **Step 4: Verify on server**

```bash
ssh klemma "docker exec deploy-api-1 python -c 'from klemma.api.adapters import _SaaSStateAdapter, _NullVault; print(\"ok\")'"
ssh klemma "docker logs deploy-worker-1 --tail 20"
```

- [ ] **Step 5: Commit all + create PR**

```bash
git add -A
git commit -m "feat: research reports in SaaS — full pipeline (#205)"
gh pr create --title "feat: research reports in SaaS (#205)" --body "..."
```
