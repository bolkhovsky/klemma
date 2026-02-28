# Fragment RAG Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add fragment-level semantic retrieval to `klemma ask` so the agent receives the top-K most relevant citation fragments as context.

**Architecture:** DB migration v5 adds embedding columns to `fragments` table. `FragmentRepository` gets save/retrieve methods for fragment embeddings. `klemma embed --fragments` batch-embeds all fragments. `build_agent_context()` accepts optional query + embeddings to inject relevant fragments into the agent prompt.

**Tech Stack:** Python 3.11, SQLite (struct.pack/unpack for BLOB vectors), existing `EmbeddingProvider` protocol, Jinja2 templates, Click CLI, pytest + unittest.mock

---

### Task 1: DB Migration v5 — fragment embedding columns

**Files:**
- Modify: `src/klemma/state.py:186` (target version + migration block)
- Test: `tests/test_repositories.py` (existing migration tests)

**Context:** Migrations live in `StateManager._migrate_schema()`. Current target is v4. Each `if version < N:` block runs once. Pattern: check `PRAGMA table_info` for idempotency, then `ALTER TABLE ADD COLUMN`. The `fragments` table schema is at line 53 of `state.py`.

**Step 1: Write the failing test**

Add to `tests/test_repositories.py`:

```python
def test_migration_v5_fragment_embedding_columns(tmp_path):
    """Migration v5 adds embedding + embedding_model to fragments."""
    from klemma.state import StateManager
    db = tmp_path / "test.db"
    sm = StateManager(db)
    import sqlite3
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(fragments)")}
    conn.close()
    assert "embedding" in cols
    assert "embedding_model" in cols
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories.py::test_migration_v5_fragment_embedding_columns -v`
Expected: FAIL — `AssertionError: assert 'embedding' in cols`

**Step 3: Write minimal implementation**

In `src/klemma/state.py`, change `target = 4` to `target = 5` (line 186), then add after the `if version < 4:` block:

```python
        if version < 5:
            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            if "embedding" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN embedding BLOB"
                )
            if "embedding_model" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN embedding_model TEXT"
                )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories.py::test_migration_v5_fragment_embedding_columns -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`
Expected: all pass (migration is additive, no breakage)

**Step 6: Commit**

```bash
git add src/klemma/state.py tests/test_repositories.py
git commit -m "feat: add DB migration v5 — fragment embedding columns"
```

---

### Task 2: Fragment embedding storage in FragmentRepository

**Files:**
- Modify: `src/klemma/repositories/fragments.py`
- Modify: `src/klemma/state.py` (facade methods)
- Test: `tests/test_repositories.py`

**Context:** `EmbeddingsStoreRepository` (at `repositories/embeddings_store.py`) stores source embeddings as `struct.pack(f"{len(embedding)}f", *embedding)` BLOBs. We need the exact same pattern for fragments. The `FragmentRepository` class is at `repositories/fragments.py`. `StateManager` exposes facade methods for all repos.

**Step 1: Write the failing tests**

Add to `tests/test_repositories.py`:

```python
def test_fragment_embedding_save_retrieve_roundtrip(tmp_path):
    """Save and retrieve a fragment embedding."""
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])
    sm.save_fragments("paper1", [{"text": "Ice forecast accuracy improved", "type": "result"}])

    # Get fragment id
    frags = sm.get_fragments(source_id="paper1")
    frag_id = frags[0]["id"]

    vec = [0.1, 0.2, 0.3, 0.4]
    sm.save_fragment_embedding(frag_id, vec, "test-model")

    result = sm.get_fragment_embeddings(model="test-model")
    assert frag_id in result
    assert len(result[frag_id]) == 4
    assert abs(result[frag_id][0] - 0.1) < 1e-5


def test_fragment_embedding_stats(tmp_path):
    """Fragment embedding coverage stats."""
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])
    sm.save_fragments("paper1", [
        {"text": "Fragment A", "type": "result"},
        {"text": "Fragment B", "type": "method"},
    ])
    frags = sm.get_fragments(source_id="paper1")

    sm.save_fragment_embedding(frags[0]["id"], [0.1, 0.2], "test-model")

    stats = sm.get_fragment_embedding_stats()
    assert stats["total"] >= 2
    assert stats["embedded"] == 1
    assert stats["models"]["test-model"] == 1
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_repositories.py::test_fragment_embedding_save_retrieve_roundtrip tests/test_repositories.py::test_fragment_embedding_stats -v`
Expected: FAIL — `AttributeError: 'StateManager' object has no attribute 'save_fragment_embedding'`

**Step 3: Write minimal implementation**

Add to `src/klemma/repositories/fragments.py`:

```python
import struct

# ... inside FragmentRepository class:

    def save_fragment_embedding(
        self, fragment_id: int, embedding: list[float], model: str
    ):
        """Store fragment embedding vector as BLOB with model name."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        with self._conn() as conn:
            conn.execute(
                "UPDATE fragments SET embedding=?, embedding_model=? WHERE id=?",
                (blob, model, fragment_id),
            )

    def get_fragment_embeddings(
        self, model: Optional[str] = None
    ) -> dict[int, list[float]]:
        """Get all fragment embeddings, optionally filtered by model.

        Returns {fragment_id: vector}.
        """
        with self._conn() as conn:
            if model:
                cur = conn.execute(
                    "SELECT id, embedding FROM fragments "
                    "WHERE embedding IS NOT NULL AND embedding_model=?",
                    (model,),
                )
            else:
                cur = conn.execute(
                    "SELECT id, embedding FROM fragments WHERE embedding IS NOT NULL"
                )
            result = {}
            for row in cur.fetchall():
                blob = row["embedding"]
                n = len(blob) // 4
                result[row["id"]] = list(struct.unpack(f"{n}f", blob))
            return result

    def get_fragment_embedding_stats(self) -> dict:
        """Get fragment embedding coverage stats."""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM fragments"
            ).fetchone()["cnt"]
            embedded = conn.execute(
                "SELECT COUNT(*) as cnt FROM fragments WHERE embedding IS NOT NULL"
            ).fetchone()["cnt"]
            models: dict[str, int] = {}
            cur = conn.execute(
                "SELECT embedding_model, COUNT(*) as cnt FROM fragments "
                "WHERE embedding IS NOT NULL GROUP BY embedding_model"
            )
            for row in cur.fetchall():
                models[row["embedding_model"] or "unknown"] = row["cnt"]
            return {"total": total, "embedded": embedded, "models": models}
```

Add facade methods to `src/klemma/state.py` (in the Fragment delegation section, around line 300):

```python
    def save_fragment_embedding(self, fragment_id: int, embedding: list[float], model: str):
        return self.fragments.save_fragment_embedding(fragment_id, embedding, model)

    def get_fragment_embeddings(self, model: Optional[str] = None) -> dict[int, list[float]]:
        return self.fragments.get_fragment_embeddings(model)

    def get_fragment_embedding_stats(self) -> dict:
        return self.fragments.get_fragment_embedding_stats()
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_repositories.py::test_fragment_embedding_save_retrieve_roundtrip tests/test_repositories.py::test_fragment_embedding_stats -v`
Expected: PASS

**Step 5: Run full suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`
Expected: all pass

**Step 6: Commit**

```bash
git add src/klemma/repositories/fragments.py src/klemma/state.py tests/test_repositories.py
git commit -m "feat: add fragment embedding storage and retrieval"
```

---

### Task 3: Fragment retrieval by cosine similarity

**Files:**
- Modify: `src/klemma/repositories/fragments.py`
- Modify: `src/klemma/state.py` (facade)
- Test: `tests/test_repositories.py`

**Context:** The `cosine_similarity()` function lives in `src/klemma/embeddings.py`. Gap semantic reranking (`repositories/gaps.py:118`) imports it the same way. Retrieval loads all fragment embeddings in memory, computes cosine vs query vector, returns top-K fragment dicts enriched with similarity score.

**Step 1: Write the failing test**

Add to `tests/test_repositories.py`:

```python
def test_retrieve_similar_fragments(tmp_path):
    """Top-K retrieval returns fragments ranked by cosine similarity."""
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])
    sm.save_fragments("paper1", [
        {"text": "Ice forecast validation methods", "type": "method", "section": "1.1"},
        {"text": "Neural network architecture", "type": "method", "section": "2.1"},
        {"text": "Satellite data processing", "type": "background", "section": "1.2"},
    ])
    frags = sm.get_fragments(source_id="paper1")

    # Embed: frag[0] close to query, frag[1] orthogonal, frag[2] medium
    sm.save_fragment_embedding(frags[0]["id"], [1.0, 0.0, 0.0], "test")
    sm.save_fragment_embedding(frags[1]["id"], [0.0, 1.0, 0.0], "test")
    sm.save_fragment_embedding(frags[2]["id"], [0.7, 0.3, 0.0], "test")

    query_vec = [1.0, 0.0, 0.0]  # closest to frag[0]
    results = sm.retrieve_similar_fragments(query_vec, top_k=2, model="test")

    assert len(results) == 2
    assert results[0]["id"] == frags[0]["id"]
    assert results[0]["similarity"] > 0.99
    assert results[1]["id"] == frags[2]["id"]
    # Each result has fragment fields
    assert "fragment_text" in results[0]
    assert "citekey" in results[0]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories.py::test_retrieve_similar_fragments -v`
Expected: FAIL — `AttributeError: 'StateManager' object has no attribute 'retrieve_similar_fragments'`

**Step 3: Write minimal implementation**

Add to `FragmentRepository` in `src/klemma/repositories/fragments.py`:

```python
    def retrieve_similar_fragments(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        model: Optional[str] = None,
    ) -> list[dict]:
        """Retrieve top-K fragments by cosine similarity to query vector.

        Returns fragment dicts enriched with 'similarity' and 'citekey' fields.
        """
        from ..embeddings import cosine_similarity

        all_emb = self.get_fragment_embeddings(model=model)
        if not all_emb:
            return []

        # Compute similarities
        scored: list[tuple[int, float]] = []
        for frag_id, vec in all_emb.items():
            sim = cosine_similarity(query_embedding, vec)
            scored.append((frag_id, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_ids = scored[:top_k]

        if not top_ids:
            return []

        # Fetch fragment details
        with self._conn() as conn:
            placeholders = ",".join("?" * len(top_ids))
            id_list = [t[0] for t in top_ids]
            cur = conn.execute(
                f"""SELECT f.*, s.id as citekey
                    FROM fragments f
                    JOIN sources s ON f.source_id = s.id
                    WHERE f.id IN ({placeholders})""",
                id_list,
            )
            frag_map = {row["id"]: dict(row) for row in cur.fetchall()}

        # Build result in similarity order
        results = []
        for frag_id, sim in top_ids:
            if frag_id in frag_map:
                frag = frag_map[frag_id]
                frag["similarity"] = round(sim, 4)
                # Remove BLOB from output
                frag.pop("embedding", None)
                results.append(frag)
        return results
```

Add facade method to `src/klemma/state.py`:

```python
    def retrieve_similar_fragments(
        self, query_embedding: list[float], top_k: int = 10, model: Optional[str] = None
    ) -> list[dict]:
        return self.fragments.retrieve_similar_fragments(query_embedding, top_k, model)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories.py::test_retrieve_similar_fragments -v`
Expected: PASS

**Step 5: Run full suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`

**Step 6: Commit**

```bash
git add src/klemma/repositories/fragments.py src/klemma/state.py tests/test_repositories.py
git commit -m "feat: add top-K fragment retrieval by cosine similarity"
```

---

### Task 4: `klemma embed --fragments` CLI command

**Files:**
- Modify: `src/klemma/cli.py:1000-1101` (embed command)
- Test: `tests/test_cli_embed.py`

**Context:** The existing `klemma embed` command (line 1000) backfills source embeddings. It uses `kctx.embeddings` provider and `kctx.library.entries` for title/abstract. For fragments, we don't need library entries — we embed `fragment_text` directly. The `--fragments` flag is a new mode.

**Step 1: Write the failing test**

Add to `tests/test_cli_embed.py`:

```python
def test_embed_fragments_flag(tmp_path, monkeypatch):
    """klemma embed --fragments embeds un-embedded fragments."""
    from unittest.mock import MagicMock, patch
    from click.testing import CliRunner
    from klemma.cli import main
    from klemma.state import StateManager

    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(db)
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1")
    sm.save_fragments("paper1", [
        {"text": "Ice forecast accuracy", "type": "result"},
        {"text": "Neural networks for prediction", "type": "method"},
    ])

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [0.1, 0.2, 0.3]

    mock_ctx = MagicMock()
    mock_ctx.state = sm
    mock_ctx.embeddings = mock_emb
    mock_ctx.config = MagicMock()
    mock_ctx.library = None

    runner = CliRunner()
    with patch("klemma.cli._get_context", return_value=mock_ctx):
        result = runner.invoke(main, ["embed", "--fragments"])

    assert result.exit_code == 0
    assert "Embedded: 2" in result.output
    assert mock_emb.embed.call_count == 2

    # Verify fragments now have embeddings
    stats = sm.get_fragment_embedding_stats()
    assert stats["embedded"] == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_embed.py::test_embed_fragments_flag -v`
Expected: FAIL — `Error: No such option: --fragments`

**Step 3: Write minimal implementation**

In `src/klemma/cli.py`, modify the `embed` command. Add `--fragments` option (around line 1000):

```python
@main.command()
@click.argument("citekeys", required=False, nargs=-1)
@click.option("--dry-run", is_flag=True, help="Show how many would be embedded without calling API")
@click.option("--backend", type=click.Choice(["s2", "local", "openai"]), help="Override embedding backend")
@click.option("--fragments", is_flag=True, help="Embed fragments instead of sources")
@click.pass_context
def embed(ctx, citekeys, dry_run, backend, fragments):
```

Then add a new branch at the start of the function body, after the embedding provider setup (after the `if not emb and not dry_run:` block around line 1028):

```python
    if fragments:
        # Fragment embedding mode
        all_frags = state.get_fragments(limit=100000)
        # Filter to un-embedded fragments
        candidates = [f for f in all_frags if not f.get("embedding")]
        if not candidates:
            console.print("[green]All fragments already have embeddings.[/green]")
            return
        if dry_run:
            console.print(f"[blue]Would embed {len(candidates)} fragments[/blue]")
            return

        embedded = 0
        failed = 0
        from rich.progress import Progress
        with Progress(console=console) as progress:
            task = progress.add_task("Embedding fragments...", total=len(candidates))
            for frag in candidates:
                try:
                    vec = emb.embed(frag["fragment_text"])
                    if vec:
                        state.save_fragment_embedding(frag["id"], vec, emb.model_name)
                        embedded += 1
                    else:
                        failed += 1
                except Exception as e:
                    console.print(f"  [red]Fragment {frag['id']}: {e}[/red]")
                    failed += 1
                progress.advance(task)

        console.print(f"\n[green]Embedded: {embedded}[/green]", end="")
        if failed:
            console.print(f" | [red]Failed: {failed}[/red]", end="")
        console.print()
        return
```

Note: the `get_fragments(limit=100000)` call does not return embedding BLOBs in the current implementation (the SELECT does not include the `embedding` column check). We need to check if fragments have embeddings. The simplest way is to use the fragment_embedding_stats to get the count, or adjust the query. Since `get_fragments` returns `f.*`, the embedding column WILL be present (after v5 migration), so `f.get("embedding")` will work.

Actually, the `get_fragments` query does `SELECT f.*` which includes the embedding BLOB — that would load all BLOBs into memory unnecessarily. Better approach: query just the IDs of un-embedded fragments directly. Add a helper to `FragmentRepository`:

```python
    def get_unembedded_fragments(self, limit: int = 100000) -> list[dict]:
        """Get fragments without embeddings. Returns id, source_id, fragment_text."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT f.id, f.source_id, f.fragment_text, s.id as citekey
                   FROM fragments f
                   JOIN sources s ON f.source_id = s.id
                   WHERE f.embedding IS NULL
                   LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
```

And facade in `state.py`:
```python
    def get_unembedded_fragments(self, limit: int = 100000) -> list[dict]:
        return self.fragments.get_unembedded_fragments(limit)
```

Then use `state.get_unembedded_fragments()` in the CLI instead of filtering `get_fragments()`.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_embed.py::test_embed_fragments_flag -v`
Expected: PASS

**Step 5: Run full suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`

**Step 6: Commit**

```bash
git add src/klemma/cli.py src/klemma/repositories/fragments.py src/klemma/state.py tests/test_cli_embed.py
git commit -m "feat: add klemma embed --fragments for fragment embedding"
```

---

### Task 5: Agent context with relevant fragments

**Files:**
- Modify: `src/klemma/skills/agent.py:70-212`
- Modify: `prompts/agent.md`
- Test: `tests/test_agent_rag.py` (new file)

**Context:** `build_agent_context()` takes `config, state, vault, section, chapter, ...` and returns a rendered Jinja2 prompt string. We add optional `embeddings` + `query` params. When both present: embed query → retrieve top-K → pass to template. The `agent.md` template gets a new `## Relevant Fragments` section.

**Step 1: Write the failing test**

Create `tests/test_agent_rag.py`:

```python
"""Tests for fragment RAG in agent context."""

from unittest.mock import MagicMock, patch

import pytest


def _make_state(tmp_path):
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1")
    sm.save_fragments("paper1", [
        {"text": "Ice forecast accuracy improved by 15%", "type": "result", "section": "1.1",
         "citation_intent": "result_comparison"},
    ])
    frags = sm.get_fragments(source_id="paper1")
    sm.save_fragment_embedding(frags[0]["id"], [1.0, 0.0, 0.0], "test-model")
    return sm


def test_agent_context_with_fragments(tmp_path):
    """When embeddings + query provided, agent context includes relevant fragments."""
    sm = _make_state(tmp_path)

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [1.0, 0.0, 0.0]

    from klemma.skills.agent import build_agent_context
    from klemma.config import KlemmaConfig

    config = KlemmaConfig()

    context = build_agent_context(
        config, sm, MagicMock(),
        embeddings=mock_emb,
        query="ice forecast validation",
    )

    assert "Relevant Fragments" in context
    assert "Ice forecast accuracy improved by 15%" in context
    assert "paper1" in context


def test_agent_context_without_embeddings(tmp_path):
    """Without embeddings, no fragment section in context."""
    sm = _make_state(tmp_path)

    from klemma.skills.agent import build_agent_context
    from klemma.config import KlemmaConfig

    config = KlemmaConfig()

    context = build_agent_context(config, sm, MagicMock())

    assert "Relevant Fragments" not in context


def test_agent_context_no_fragment_embeddings(tmp_path):
    """With embeddings but no fragment embeddings, no fragment section."""
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [1.0, 0.0, 0.0]

    from klemma.skills.agent import build_agent_context
    from klemma.config import KlemmaConfig

    config = KlemmaConfig()
    context = build_agent_context(
        config, sm, MagicMock(),
        embeddings=mock_emb,
        query="test query",
    )

    assert "Relevant Fragments" not in context
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_rag.py -v`
Expected: FAIL — `TypeError: build_agent_context() got an unexpected keyword argument 'embeddings'`

**Step 3: Implement**

In `src/klemma/skills/agent.py`, add `embeddings` and `query` params to `build_agent_context()`:

```python
def build_agent_context(
    config: KlemmaConfig,
    state: StateManager,
    vault: VaultAdapter,
    section: Optional[str] = None,
    chapter: Optional[int] = None,
    project: Optional[ProjectConfig] = None,
    dissertation_context: str = "",
    klemma_home: Optional[Path] = None,
    project_name: str = "",
    project_root: Optional[Path] = None,
    embeddings=None,
    query: Optional[str] = None,
) -> str:
```

Before the `# Render prompt` block (around line 173), add fragment retrieval:

```python
    # Fragment RAG: retrieve relevant fragments if embeddings + query available
    relevant_fragments = []
    if embeddings and query:
        try:
            query_vec = embeddings.embed(query)
            if query_vec:
                relevant_fragments = state.retrieve_similar_fragments(
                    query_vec, top_k=10, model=embeddings.model_name
                )
        except Exception:
            logger.debug("Fragment RAG retrieval failed", exc_info=True)
```

Add `relevant_fragments=relevant_fragments` to the `Template(raw).render(...)` call.

In `prompts/agent.md`, add between `## Sources` and `## Today's Plan`:

```jinja2
{% if relevant_fragments %}

## Relevant Fragments ({{ relevant_fragments | length }})

{% for f in relevant_fragments %}
- @{{ f.citekey }} [{{ f.citation_intent or "?" }}] (sim={{ f.similarity }}): {{ f.fragment_text[:300] }}
{% endfor %}
{% endif %}
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_rag.py -v`
Expected: PASS

**Step 5: Run full suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`

**Step 6: Commit**

```bash
git add src/klemma/skills/agent.py prompts/agent.md tests/test_agent_rag.py
git commit -m "feat: inject relevant fragments into agent context via RAG"
```

---

### Task 6: Wire embeddings into `klemma ask` CLI

**Files:**
- Modify: `src/klemma/cli.py:1792-1827` (ask command)

**Context:** The `ask` command currently calls `build_agent_context()` without embeddings. The `kctx` object (KlemmaContext, defined in `context.py`) already has `embeddings: Optional[EmbeddingProvider]`. We need to pass `embeddings=kctx.embeddings` and `query=query` to `build_agent_context()`.

**Step 1: Implement**

In `src/klemma/cli.py`, modify the `ask` function (around line 1803):

```python
    with console.status("Сборка контекста исследования", spinner="dots"):
        context = build_agent_context(
            cfg, state, vault, section=section, chapter=chapter,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_name=kctx.project_name,
            project_root=kctx.project_root,
            embeddings=kctx.embeddings,
            query=query,
        )
```

Add a log line after context is built to show fragment RAG status:

```python
    # Show RAG status
    if kctx.embeddings:
        frag_stats = state.get_fragment_embedding_stats()
        if frag_stats["embedded"] > 0:
            console.print(f"[dim]RAG: {frag_stats['embedded']} fragment embeddings available[/dim]")
        else:
            console.print("[dim]RAG: no fragment embeddings (run klemma embed --fragments)[/dim]")
```

**Step 2: Run full suite**

Run: `ruff check src/ tests/ && python -m pytest tests/ -q`
Expected: all pass (no new tests needed — CLI wiring only, tested via test_agent_rag.py)

**Step 3: Commit**

```bash
git add src/klemma/cli.py
git commit -m "feat: wire fragment RAG into klemma ask command"
```

---

### Task 7: Update CLAUDE.md documentation

**Files:**
- Modify: `src/klemma/CLAUDE.md` (state.py section — migration v5)
- Modify: `src/klemma/evaluation/CLAUDE.md` (no change needed — RAG is in skills)
- Modify: `src/klemma/skills/CLAUDE.md` (agent.py section)
- Modify: `src/klemma/repositories/CLAUDE.md` (fragments.py new methods)
- Modify: `prompts/CLAUDE.md` (agent.md new variable)
- Modify: `tests/CLAUDE.md` (new test file)

**Changes:**

1. `src/klemma/CLAUDE.md`: Under `state.py` section, change `(currently v4)` to `(currently v5)`. Under Tables → `fragments`, add: `` `embedding` BLOB float32, `embedding_model` TEXT ``

2. `src/klemma/repositories/CLAUDE.md`: Under `fragments.py`, update line count, add:
   - `save_fragment_embedding()` — store vector BLOB
   - `get_fragment_embeddings()` — return `{fragment_id: vector}`
   - `get_fragment_embedding_stats()` — coverage stats
   - `get_unembedded_fragments()` — fragments missing embeddings
   - `retrieve_similar_fragments()` — top-K cosine retrieval

3. `src/klemma/skills/CLAUDE.md`: Under `agent.py`, update line count, add:
   - `build_agent_context(... embeddings?, query?)` — optional fragment RAG: embeds query → top-K retrieval → injects into prompt

4. `prompts/CLAUDE.md`: Under `agent.md` variables, add: `relevant_fragments` (list of fragment dicts with citekey, fragment_text, citation_intent, similarity)

5. `tests/CLAUDE.md`: Add `test_agent_rag.py` entry

**Commit:**

```bash
git add src/klemma/CLAUDE.md src/klemma/repositories/CLAUDE.md src/klemma/skills/CLAUDE.md prompts/CLAUDE.md tests/CLAUDE.md
git commit -m "docs: update CLAUDE.md for fragment RAG (Epic-B)"
```

---

### Task 8: Create PR and update Epic-B issue

**Files:** None (GitHub operations only)

**Steps:**

1. Push branch:
```bash
git push -u origin feat/fragment-rag
```

2. Create PR:
```bash
gh pr create --title "feat: fragment RAG for klemma ask (Epic-B #28)" --body "$(cat <<'EOF'
## Summary

- DB migration v5: fragment embedding columns
- Fragment embedding storage/retrieval in FragmentRepository
- Top-K cosine similarity retrieval for fragments
- `klemma embed --fragments` CLI command
- Agent context enhanced with relevant fragments via RAG
- Backward compatible: no fragment embeddings = current behavior

Part of #28

## Release Note

### Problem
`klemma ask` dumps source metadata (citekey, quality, fragment count) into the agent prompt but not actual fragment text. The agent answers from general knowledge instead of grounded citations. With 2340+ extracted fragments, we have rich evidence that's invisible to the agent.

### Academic Foundation
ILCiteR (Sahu & Ostendorf 2022) establishes that fragment-level embeddings are the highest-quality lever for citation recommendation. SPECTER (Cohan et al. 2020) and SciRepEval (Singh et al. 2023) validate that scientific text embeddings generalize well to short passages. PaperQA (Lala et al. 2023) demonstrates that focused fragment context outperforms full-document retrieval for research QA.

### Implementation
- Migration v5 adds `embedding BLOB` + `embedding_model TEXT` to fragments table
- `FragmentRepository` gains 5 new methods: save/get/stats/unembedded/retrieve
- `retrieve_similar_fragments()` computes in-memory cosine similarity (7.5MB for 2500 fragments at 768-dim)
- `build_agent_context()` accepts `embeddings` + `query`, embeds query, retrieves top-10 fragments
- `agent.md` template gains `## Relevant Fragments` section with citekey, intent, similarity score
- `klemma embed --fragments` batch-embeds all un-embedded fragments

### Results
- New tests: X tests in test_repositories.py + test_agent_rag.py + test_cli_embed.py
- All existing 472+ tests pass
- Lint clean (ruff)
- Backward compatible: zero behavior change without fragment embeddings

## Test plan

- [ ] `python -m pytest tests/ -q` — all pass
- [ ] `ruff check src/ tests/` — clean
- [ ] Manual: `klemma embed --fragments --dry-run` shows count
- [ ] Manual: `klemma ask "ice forecast"` shows RAG status line

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

3. Update Epic-B #28 checkboxes.
