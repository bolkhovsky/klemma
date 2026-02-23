# Klemma Modernization Roadmap: Implementation Plan

## Context

Klemma — CLI-инструмент для академического письма (управление литературой, AI-извлечение фрагментов, отслеживание пробелов). На основе анализа 5 научных работ (SPECTER, Citation Intent, Literature Graph, Citation Recommendation, SciRepEval) создан roadmap модернизации: 21 фича, 5 фаз, переход от эвристик к научно обоснованным методам.

**Двойная цель**: каждый шаг реализации (a) улучшает klemma и (b) генерирует результаты для научной статьи. Статья в стадии outline, все результаты пойдут в секции 4-5.

**Ключевое решение**: MCP восстанавливается минимально (~200 строк) — только MCPClient + ToolRegistry, без старого discovery pipeline. Писать заново, НЕ восстанавливать из git history (MCP SDK мог измениться).

## Pre-requisites

1. **Merge feature/outline-command в master** (5 коммитов: outline command + paper project support)
2. **Обновить PROJECT_LOG.md** (outline command, MCP removal)
3. **Создать `results/` в klemma-paper** для структурированных метрик before/after
4. **Зафиксировать baseline** на текущей кодовой базе (количество строк, тесты, DB schema)

## Branch Strategy

```
master ← feature/outline-command (merge first)
       ← modernize/phase-0-intents (Phase 0 + 1)
       ← modernize/phase-2-embeddings (Phase 2, after Sprint 1 merge)
       ← modernize/phase-3-graph (Phase 3, after Sprint 1 merge, before Sprint 2 done)
       ← modernize/phase-4-mcp (Phase 4, after Sprint 2)
       ← modernize/phase-5-advanced (Phase 5, after all)
```

Каждая фаза — отдельный PR. Внутри фазы — атомарные коммиты по шагам.

## DB Migration Strategy

**ВАЖНО**: `_migrate_schema()` НЕ существует в текущем коде — нужно создать.

Использовать `PRAGMA user_version` для версионирования схемы (а не per-column PRAGMA table_info):

```python
def _migrate_schema(self, conn):
    """Idempotent schema migrations using PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        conn.execute("ALTER TABLE fragments ADD COLUMN citation_intent TEXT")
        conn.execute("ALTER TABLE reference_gaps ADD COLUMN citation_intent TEXT")
    if version < 2:
        conn.execute("ALTER TABLE sources ADD COLUMN embedding BLOB")
        conn.execute("ALTER TABLE sources ADD COLUMN embedding_model TEXT")
    if version < 3:
        conn.execute("""CREATE TABLE IF NOT EXISTS citation_links (
            source_id TEXT NOT NULL,
            target_citekey TEXT,
            target_title_hash TEXT NOT NULL,
            target_title TEXT NOT NULL,
            target_authors TEXT,
            target_year INTEGER,
            citation_intent TEXT,
            in_library BOOLEAN DEFAULT 0,
            UNIQUE(source_id, target_title_hash)
        )""")

    conn.execute(f"PRAGMA user_version = 3")
```

Вызывается из `_init_db()` после создания таблиц. Идемпотентно, быстро (один PRAGMA check vs N table_info).

## Dependency Strategy

- `numpy` — optional extra `[embeddings]` (30MB, не нужен всем пользователям)
- `sentence-transformers` — optional extra `[local-embeddings]` (numpy + sentence-transformers)
- `mcp` — optional extra `[mcp]`, lazy import
- Core klemma остаётся лёгким (~7 deps)

**Lazy import pattern** для optional deps:
```python
def _ensure_numpy():
    try:
        import numpy as np
        return np
    except ImportError:
        raise ImportError("Install klemma[embeddings] for embedding support: pip install klemma[embeddings]")
```

---

# Sprint 1: Citation Intent (Phase 0 + Phase 1)

**Ветка**: `modernize/phase-0-intents`
**Цель**: Обогатить модель фрагментов citation intent, ввести intent-weighted scoring
**НР**: НР1 (модель фрагментов), НР2 (scoring gaps)
**Новые тест-файлы**: `tests/test_intent_scoring.py`

## Step 0.0: Create `_migrate_schema()` Infrastructure

**Файлы**: `src/klemma/state.py`

**Что делать**:
1. Создать метод `_migrate_schema(self, conn)` с `PRAGMA user_version` tracking
2. Вызывать из `_init_db()` после CREATE TABLE statements
3. Начать с version=0 (текущая schema, no migrations yet)

**Тестирование**:
- Unit: in-memory SQLite → `_init_db()` → verify `PRAGMA user_version = 0`
- Unit: повторный вызов — идемпотентность
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

## Step 0.1: Citation Intent в extract prompt

**Файлы**: `prompts/extract.md`, `src/klemma/literature/models.py`

**Что делать**:
- Добавить `citation_intent` в JSON-схему extract.md: `{"background", "method", "result_comparison"}`
- Добавить поле `citation_intent: Optional[Literal["background", "method", "result_comparison"]] = None` в модель `Fragment`

**Baseline (ДО)**:
- Зафиксировать текущий extract.md prompt
- Запустить `klemma process` на 8-10 тестовых источниках (temperature=0), сохранить JSON-ответы
- Записать: кол-во фрагментов, типы, структуру JSON

**Тестирование**:
- Unit: тест модели Fragment с citation_intent (valid values + None + invalid → ValidationError)
- Schema: тест что JSON-пример из промпта проходит pydantic-парсинг
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Запустить `klemma process` на тех же 8-10 источниках с новым промптом (temperature=0)
- Сравнить: появились ли citation_intent в ответах, распределение background/method/result
- Записать результаты в `klemma-paper/results/phase0_intent.md`

**Для статьи**: Таблица "Распределение citation intent" → §4.1 (extraction pipeline), подтверждение Cohan 2019

## Step 0.2: Scaffold Prompting

**Файлы**: `prompts/extract.md`

**Что делать**:
- Добавить два scaffold-блока ПЕРЕД основной инструкцией:
  1. Structure Analysis: определи структуру оригинальной статьи (разделы, методология)
  2. Citation Worthiness: найди утверждения с верифицируемыми фактами (числа, методы, результаты)

**Baseline (ДО)**:
- Сохранить результаты 0.1 как baseline для scaffold сравнения

**Тестирование**:
- Manual A/B: те же 8-10 источников → scaffold prompt vs baseline (0.1), temperature=0
- Метрики: кол-во фрагментов, средний relevance, покрытие разделов
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Таблица: Source | Fragments(before) | Fragments(after) | Avg Relevance(before/after)
- Записать в `klemma-paper/results/phase0_scaffold.md`

**Для статьи**: §4.1 — scaffold prompting как реализация Cohan 2019 auxiliary tasks

## Step 0.3: Intent-Aware Gap Extraction

**Файлы**: `prompts/annotate.md`

**Что делать**:
- Добавить `citation_intent` в формат key_references в annotate.md
- Intent ∈ {"background", "method", "result_comparison"} — как источник цитирует эту работу

**Baseline (ДО)**:
- Зафиксировать текущий annotate.md prompt
- Сохранить пример annotate-ответа для 2 источников

**Тестирование**:
- Schema: JSON-пример из промпта парсится в AnnotationResult (key_references с intent)
- Manual: annotate 2 источника, проверить intent в key_references
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Распределение intent в key_references
- Записать в `klemma-paper/results/phase0_annotate_intent.md`

## Step 1.1: DB Schema — citation_intent

**Файлы**: `src/klemma/state.py`, `src/klemma/skills/extractor.py`, `src/klemma/literature/note_factory.py`

**Что делать**:
1. В `_migrate_schema()`: version 1 → `ALTER TABLE fragments ADD COLUMN citation_intent TEXT` + `ALTER TABLE reference_gaps ADD COLUMN citation_intent TEXT`
2. В `save_fragments()`: добавить citation_intent в INSERT (сейчас INSERT имеет 8 полей, state.py ~line 495-509)
3. В `_save_reference_gaps()`: сохранять citation_intent из annotate response

**ВАЖНО**: Координация с Step 0.1 — модель Fragment уже имеет поле, теперь нужно персистить его в DB.

**Backward compatibility**: Старые fragments с NULL citation_intent — scoring должен работать как раньше (intent_weight = 1.0 для NULL).

**Baseline (ДО)**:
- `PRAGMA table_info(fragments)` — зафиксировать текущую схему
- `PRAGMA table_info(reference_gaps)` — зафиксировать текущую схему
- Кол-во записей в обеих таблицах

**Тестирование**:
- Unit: StateManager с in-memory SQLite — проверить миграцию (version 0 → 1)
- Unit: save_fragments() с citation_intent → SELECT проверка
- Unit: save_fragments() БЕЗ citation_intent → NULL, scoring работает
- Unit: save_reference_gaps() с citation_intent → SELECT проверка
- **Regression**: get_reference_gaps() с NULL intents → ordering идентичен baseline
- Integration: `klemma process <citekey>` → проверить fragments.citation_intent в DB
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Новая схема таблиц (PRAGMA table_info)
- % фрагментов с заполненным citation_intent после re-processing
- Записать в `klemma-paper/results/phase1_schema.md`

**Для статьи**: §4.2 — расширение ER-диаграммы, новые поля для intent-aware scoring

## Step 1.2: Intent-Weighted Gap Scoring

**Файлы**: `src/klemma/state.py` (метод `get_reference_gaps()`)

**Что делать**:
- Добавить intent-weight к формуле скоринга:
  ```
  score = count × avg_quality × section_weight × intent_weight
  intent_weight = AVG(CASE WHEN citation_intent IS NULL THEN 1.0
                       WHEN citation_intent = 'method' THEN 3.0
                       WHEN citation_intent = 'result_comparison' THEN 2.0
                       ELSE 1.0 END)
  ```
- **NULL handling**: NULL intent → weight 1.0 (backward compatible, scoring unchanged for old data)

**Baseline (ДО)**:
- Запустить `klemma status --verbose` → зафиксировать top-10 gaps с текущими scores
- Сохранить полный список gaps с scores

**Тестирование**:
- Unit: in-memory SQLite с тестовыми gaps разных intent → проверить ordering
- Unit: method-gap должен ранжироваться выше background-gap при равных прочих
- **Regression**: gaps с NULL intent → ordering идентичен Step 1.1 baseline
- Integration: `klemma status` → gap ordering
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Новый top-10 gaps vs baseline top-10 → Kendall tau корреляция
- Сколько method-gaps поднялись в ранжировании
- Записать в `klemma-paper/results/phase1_scoring.md`

**Для статьи**: §4.2 — формула скоринга с intent-weight, сравнение ранжирований, обоснование весов через Cohan 2019 (58%/29%/13%)

## Step 1.3: Intent Coverage Matrix

**Файлы**: `src/klemma/state.py`, `src/klemma/cli.py`

**Что делать**:
1. Новый метод `get_intent_coverage()` → `{section: {background: N, method: N, result_comparison: N}}`
2. Добавить матрицу в `klemma status --verbose`:
   ```
   Intent Coverage:
   Section | Background | Method | Result | Total
   §2.1    |    24      |   8    |   3    |  35
   ```

**Тестирование**:
- Unit: get_intent_coverage() с тестовыми данными
- Integration: `klemma status --verbose` → проверить вывод матрицы
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Скриншот/текст матрицы для реального проекта
- Записать в `klemma-paper/results/phase1_coverage_matrix.md`

**Для статьи**: §4.2 + §5.2 — визуализация пробелов по типу intent, обнаружение "method gaps" в конкретных разделах

---

# Sprint 2: SPECTER Embeddings (Phase 2)

**Ветка**: `modernize/phase-2-embeddings` (после merge Sprint 1)
**Цель**: Семантический поиск и scoring на базе document embeddings
**НР**: НР2 (semantic scoring), НР3 (extensible provider architecture)
**Новые тест-файлы**: `tests/test_embeddings.py`

## Step 2.1: EmbeddingProvider Protocol

**Файлы**: новый `src/klemma/embeddings.py`, `src/klemma/config.py`, `src/klemma/context.py`

**Что делать**:
1. Создать `embeddings.py`:
   - `EmbeddingProvider` Protocol: `embed(title, abstract) -> Optional[list[float]]`, `dim: int`
   - `SemanticScholarEmbeddings`: S2 API `/paper/search?fields=embedding` (бесплатно)
     - **Rate limiting**: 100 req/5min (unauthenticated). Добавить `time.sleep()` throttling.
   - `LocalSPECTER`: sentence-transformers `allenai/specter2` (lazy import)
   - `OpenAIEmbeddings`: text-embedding-3-small (fallback)
   - `create_embeddings(config) -> Optional[EmbeddingProvider]` factory
2. Добавить `EmbeddingsConfig` в config.py: backend, model, cache
3. Добавить `embeddings: Optional[EmbeddingProvider]` в KlemmaContext
4. **Добавить `"embeddings"` в `_INHERITED_KEYS`** (config.py line 23, рядом с `{"obsidian", "zotero", "ai"}`) — чтобы child projects наследовали embedding config

**Dimension safety**: Хранить `embedding_model` рядом с BLOB. Запрещать cosine similarity между embeddings из разных моделей (768 vs 1536).

**Baseline (ДО)**:
- Зафиксировать отсутствие семантического поиска как baseline

**Тестирование**:
- Unit: MockEmbeddingProvider → тест protocol compliance
- Unit: SemanticScholarEmbeddings → mock HTTP → verify request/response parsing, throttling
- Unit: create_embeddings() → каждый backend → verify initialization
- Unit: cosine_similarity helper function
- Unit: dimension mismatch → raise ValueError
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- API call latency для S2 (avg over 10 papers)
- Embedding dimensionality verification
- Записать в `klemma-paper/results/phase2_embedding_provider.md`

**Для статьи**: §3.4 — Protocol-based extensibility (EmbeddingProvider), §4.4 — сравнение с SPECTER (Cohan 2020)

## Step 2.2: Embedding Storage

**Файлы**: `src/klemma/state.py`

**Что делать**:
1. `_migrate_schema()` version 2: `ALTER TABLE sources ADD COLUMN embedding BLOB` + `ALTER TABLE sources ADD COLUMN embedding_model TEXT`
2. `save_embedding(source_id, embedding: list[float], model: str)` — np.float32.tobytes()
3. `get_embedding(source_id) -> Optional[np.ndarray]` — np.frombuffer (lazy import numpy)
4. `get_all_embeddings(model: str = None) -> dict[str, np.ndarray]` — фильтрация по model
5. `cosine_similarity(a, b) -> float` utility (lazy import numpy)

**Тестирование**:
- Unit: save_embedding + get_embedding roundtrip (768 dims)
- Unit: get_all_embeddings with mixed (some null, different models)
- Unit: cosine_similarity — known vectors, edge cases (zero vector)
- Unit: embedding_model stored and retrievable
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- DB size before/after embedding storage (200 sources × 3KB ≈ 600KB)
- Записать в `klemma-paper/results/phase2_storage.md`

## Step 2.3: Auto-Embed + Backfill Command

**Файлы**: `src/klemma/literature/note_factory.py`, `src/klemma/cli.py`

**Что делать**:
1. В конце `annotate_source()`: если embeddings available и abstract есть → embed + save
2. Новая CLI команда `klemma embed [citekey]` — backfill для существующих источников
3. **Progress bar** (rich) для backfill
4. **Throttling** для S2 API (respect rate limits)
5. `--dry-run` flag — показать сколько будет embedded, не делать запросы

**Baseline (ДО)**:
- 0 embeddings в DB
- Список источников с abstract (сколько могут быть embedded)

**Тестирование**:
- Unit: mock embeddings → verify save_embedding called after annotate
- Unit: sources without abstract → skip, no error
- Integration: `klemma embed --dry-run` → показать сколько будет embedded
- Integration: `klemma embed` → проверить заполнение BLOB
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- % источников с embedding после backfill
- Время backfill для N источников (с timing breakdown: API calls vs DB writes)
- Записать в `klemma-paper/results/phase2_embed_coverage.md`

**Для статьи**: §5.2 — покрытие embedding в библиотеке, время обработки

## Step 2.4: Semantic Discovery (Hybrid)

**Файлы**: `src/klemma/skills/researcher.py` или новый метод

**Что делать**:
- Гибридный scoring: `combined = 0.4 × (kw_score / max_kw) + 0.6 × cosine_similarity`
- Модификация researcher/librarian skills для использования embeddings при рекомендации источников

**Baseline (ДО)**:
- Текущие рекомендации researcher (keyword-only): зафиксировать top-10 для 5 запросов

**Тестирование**:
- Unit: hybrid scoring с mock embeddings и mock keywords
- Integration: 5 тестовых запросов → сравнить top-10 keyword vs hybrid
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Precision@10 improvement (manual relevance judgments на 5 запросах)
- Записать в `klemma-paper/results/phase2_hybrid_discovery.md`

**Для статьи**: §4.4 — hybrid scoring, сравнение с keyword baseline, ссылка на Bhagavatula 2018

## Step 2.5: Semantic Gap Scoring

**Файлы**: `src/klemma/state.py` (get_reference_gaps)

**Что делать**:
- При наличии embeddings: semantic reranking через section centroid
- `final_score = heuristic_score × (0.5 + 0.5 × cosine_similarity_to_centroid)`

**Baseline (ДО)**:
- Top-20 gaps из Step 1.2 (intent-weighted)

**Тестирование**:
- Unit: mock embeddings → verify centroid calculation + reranking
- Unit: no embeddings available → fallback to heuristic score (no crash)
- Integration: `klemma status` с embeddings → gap ordering
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Kendall tau: intent-only vs intent+semantic ранжирование
- Записать в `klemma-paper/results/phase2_semantic_scoring.md`

**Для статьи**: §4.2 — трёхуровневая формула скоринга (count × quality × section × intent × semantic)

## Step 2.6: `klemma similar` Command

**Файлы**: `src/klemma/cli.py`

**Что делать**:
- `klemma similar <citekey>` — top-K семантически близких источников
- `klemma similar -s 2.3` — источники из других разделов, близкие к centroid раздела 2.3
- Показать: какие close-sources привязаны к ДРУГИМ разделам (cross-section рекомендации)

**Тестирование**:
- Unit: similarity calculation с mock embeddings
- Integration: `klemma similar <citekey>` → human-check результатов
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Пример вывода для 3 разных citekeys
- Cross-section discoveries (источники найденные для "чужих" разделов)
- Записать в `klemma-paper/results/phase2_similar.md`

**Для статьи**: §4.4 — cross-section recommendation, обнаружение скрытых связей

---

# Sprint 3: Citation Graph (Phase 3)

**Ветка**: `modernize/phase-3-graph` (после merge Sprint 1, может стартовать до окончания Sprint 2)
**Цель**: Структурный анализ библиографического пространства
**НР**: НР2 (graph-based gap detection)
**Новые тест-файлы**: `tests/test_citation_graph.py`

**ВАЖНО**: Sprint 3 зависит от Sprint 1 Step 0.3 (citation_intent в annotate), НЕ от Sprint 2 (embeddings). Оба спринта модифицируют `note_factory.py` — планировать merge аккуратно.

## Step 3.1: citation_links Table

**Файлы**: `src/klemma/state.py`, `src/klemma/literature/note_factory.py`

**Что делать**:
1. Новая таблица в `_migrate_schema()` version 3:
   ```sql
   CREATE TABLE IF NOT EXISTS citation_links (
       source_id TEXT NOT NULL,
       target_citekey TEXT,
       target_title_hash TEXT NOT NULL,  -- normalized title hash для UNIQUE
       target_title TEXT NOT NULL,
       target_authors TEXT,
       target_year INTEGER,
       citation_intent TEXT,
       in_library BOOLEAN DEFAULT 0,
       UNIQUE(source_id, target_title_hash)
   );
   ```
   **Title normalization**: `hashlib.md5(title.lower().strip().encode()).hexdigest()` для UNIQUE — избежать проблем с вариациями title (регистр, пунктуация, трункация).
2. В `annotate_source()`: сохранять ВСЕ key_references (и in_library=true, и false) в citation_links

**Baseline (ДО)**:
- Текущее отслеживание: только reference_gaps (отсутствующие в библиотеке)
- 0 in-library цитатных связей

**Тестирование**:
- Unit: save_citation_links() + get roundtrip
- Unit: UNIQUE constraint с нормализованным title (вариации не создают дубликаты)
- Unit: title hash collision handling
- Integration: `klemma process <citekey>` → citation_links populated
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Total citation_links after processing
- % in_library vs external
- Записать в `klemma-paper/results/phase3_citation_links.md`

**Для статьи**: §4.2 — переход от flat reference_gaps к citation graph (Ammar 2018)

## Step 3.2: Graph Analysis

**Файлы**: `src/klemma/state.py`

**Что делать**:
1. `get_co_cited(citekey)` — работы цитируемые вместе с данной
2. `get_citation_graph_stats()` — total links, unique targets, avg refs/source, most cited external, most connected internal
3. Добавить в `klemma status --verbose`: graph stats section

**Тестирование**:
- Unit: co-citation с тестовым графом (3 sources, 10 links)
- Unit: graph stats calculation
- Integration: `klemma status --verbose` → graph section
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Graph stats для реального проекта
- Top-5 most cited external works (bridging nodes)
- Записать в `klemma-paper/results/phase3_graph_analysis.md`

**Для статьи**: §4.2 + §5.2 — graph topology, bridging nodes, обнаружение ключевых внешних работ

## Step 3.3: Author Network

**Файлы**: `src/klemma/state.py`

**Что делать**:
- `get_key_author_groups(min_papers=2)` — группы авторов с 2+ работами в библиотеке
- Кластеризация по co-authorship
- Добавить в `klemma library --audit`: author network section

**Тестирование**:
- Unit: author grouping с тестовыми данными
- Integration: `klemma library --audit` → author groups
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

**Measurement (ПОСЛЕ)**:
- Кол-во author groups, top-5 by paper count
- Записать в `klemma-paper/results/phase3_author_network.md`

---

# Sprint 4: Minimal MCP (Phase 4)

**Ветка**: `modernize/phase-4-mcp` (после Sprint 2)
**Цель**: Минимальная MCP-инфраструктура для extensibility демонстрации
**НР**: НР3 (MCP extensibility)

**Стратегическое решение**: Писать MCPClient и ToolRegistry ЗАНОВО (не восстанавливать из git history). MCP SDK (`mcp` package) мог обновиться, старый код не имел тестов, и основная функциональность (embeddings) уже работает через EmbeddingProvider. MCP здесь — демонстрация архитектурной расширяемости для статьи.

## Step 4.0: Fresh MCP Client + ToolRegistry

**Файлы**: новый `src/klemma/tools/__init__.py`, `src/klemma/tools/client.py`, `src/klemma/tools/registry.py`

**Что делать**:
- Написать минимальный MCPClient (~100-130 строк): connect/disconnect, call_tool, list_tools
- Написать ToolRegistry (~100 строк): register/unregister server, route tool calls
- `mcp` как optional dependency `[mcp]`

**Тестирование**:
- Unit: ToolRegistry — add/list/remove servers
- Unit: MCPClient — mock server connection
- `python -m pytest tests/ -q`
- `ruff check src/ tests/`

## Step 4.1: SPECTER MCP Server

**Файлы**: новый `specter_mcp/` (отдельный пакет или внутри klemma)

**Что делать**:
- MCP server с tools: embed_paper, find_similar, batch_embed
- Два бэкенда: S2 API proxy или local sentence-transformers

**Тестирование**:
- Unit: embed_paper → verify embedding dimensions
- Integration: klemma → MCP server → embed → verify
- `ruff check src/ tests/`

## Step 4.2: S2 Citation Intents via MCP

**Файлы**: MCP server extension

**Что делать**:
- Tool: get_citation_intents(paper_id) → intents from S2 API
- Обогащение reference_gaps S2-интентами (ground truth vs LLM-predicted)

**Тестирование**:
- Сравнить: LLM-predicted intent vs S2 API intent → accuracy metric
- `ruff check src/ tests/`

**Measurement**:
- Intent prediction accuracy (LLM vs S2 ground truth)
- Записать в `klemma-paper/results/phase4_intent_accuracy.md`

**Для статьи**: §4.1 + §5.3 — validation LLM-predicted intents against S2 ground truth

---

# Sprint 5: Advanced Features (Phase 5) — STRETCH GOALS

**Ветка**: `modernize/phase-5-advanced`
**Цель**: Визуализация, citation worthiness, продвинутые методы
**НР**: Все НР + §5 статьи

**ПРИМЕЧАНИЕ**: Sprint 5 — stretch goals. Реализация по приоритету, при наличии времени. Оценка ~7 дней может быть оптимистичной. Бюджет +50% запаса.

## Step 5.2: Contrastive LLM Evaluation (тривиальный, первый)

**Файлы**: промпты researcher/librarian

**Что делать**:
- При оценке кандидата передавать контрастные примеры: уже в библиотеке + отклонённые
- Вопрос к LLM: "Добавляет ли кандидат УНИКАЛЬНУЮ ценность?"

## Step 5.4: Semantic Gap Disambiguation

**Файлы**: `src/klemma/state.py` (resolve_gaps)

**Что делать**:
- При >1 кандидате: SPECTER cosine → выбрать ближайшего
- При 0 кандидатов + embeddings: fuzzy semantic match (threshold 0.85)

## Step 5.1: `klemma visualize`

**Файлы**: `src/klemma/cli.py`, новый `src/klemma/visualization.py`

**Что делать**:
- Embedding space: t-SNE/UMAP, точки=источники, цвет=раздел, размер=quality
- Citation graph: DOT export
- Экспорт: HTML (plotly), PNG (matplotlib)

## Step 5.3: Citation Worthiness (`klemma check-draft`)

**Файлы**: новый `src/klemma/skills/draft_checker.py`

**Что делать**:
- Читает черновик раздела → LLM находит утверждения без ссылок → рекомендует источники по embedding similarity

---

# Paper Documentation Strategy

## Структура результатов

```
klemma-paper/results/
├── baseline.md              — snapshot до начала модернизации
├── phase0_intent.md         — citation intent в промптах
├── phase0_scaffold.md       — scaffold prompting A/B
├── phase0_annotate_intent.md
├── phase1_schema.md         — расширение DB schema
├── phase1_scoring.md        — intent-weighted scoring comparison
├── phase1_coverage_matrix.md
├── phase2_embedding_provider.md
├── phase2_storage.md
├── phase2_embed_coverage.md
├── phase2_hybrid_discovery.md
├── phase2_semantic_scoring.md
├── phase2_similar.md
├── phase3_citation_links.md
├── phase3_graph_analysis.md
├── phase3_author_network.md
├── phase4_intent_accuracy.md
└── summary.md               — consolidated results table
```

## Формат каждого results файла

```markdown
---
step: "X.Y"
date: YYYY-MM-DD
sprint: N
paper_sections: ["§4.1", "§5.2"]
---
# Step X.Y: <Name>
## Baseline (before)
<metrics, examples, screenshots>
## Implementation
<what was changed, files, LOC delta>
## Results (after)
<new metrics, comparison with baseline>
## Delta
<quantitative improvement>
## Paper Section
<which section this feeds into, specific text/figure to add>
```

## Mapping результатов к секциям статьи

| Step | Paper Section | What to Document |
|------|---------------|-----------------|
| 0.1-0.2 | §4.1 (extraction pipeline) | Scaffold prompting, intent classification |
| 0.3, 1.1-1.3 | §4.2 (gap tracking) | Intent-weighted scoring formula, coverage matrix |
| 2.1-2.3 | §3.4 (architecture) + §4.4 | EmbeddingProvider protocol, SPECTER integration |
| 2.4-2.5 | §4.4 (discovery) + §5.2 | Hybrid scoring comparison with baseline |
| 2.6 | §4.4 | Cross-section recommendation examples |
| 3.1-3.3 | §4.2 + §5.2 | Citation graph stats, co-citation analysis |
| 4.0-4.2 | §3.5 (MCP) + §5.3 | MCP architecture, intent validation |
| 5.1 | Рис. 5 (new figure) | Embedding space visualization |
| 5.3 | §4.4 | Citation worthiness examples |

---

# Verification Plan

## Git Checkpoint After EVERY Step

После КАЖДОГО шага (0.0, 0.1, 0.2, ... 5.3) — обязательный git checkpoint:
```bash
git add -A
git commit -m "modernize: step X.Y — <description>"
git push origin <branch>
```
Это создаёт точку отката и позволяет отслеживать прогресс. Checkpoint = атомарный коммит с работающим кодом.

## After Each Sprint

1. `ruff check src/ tests/` — no lint errors
2. `python -m pytest tests/ -q` — all tests pass
3. `klemma status` — works with new features on real project
4. Results file committed to klemma-paper/results/
5. Commit + push sprint branch
6. PR review

## End-to-End Validation (after all sprints)

1. Full re-process: `klemma process` on 10+ sources with all new features
2. `klemma status --verbose` — shows intent matrix + graph stats + embeddings count
3. `klemma similar <citekey>` — returns semantically close sources
4. `klemma embed` — backfills all existing sources
5. Compare full pipeline output vs baseline (from results/baseline.md)
6. Generate summary table for the paper

## Key Metrics to Track Across All Sprints

| Metric | Baseline | After Phase 0+1 | After Phase 2 | After Phase 3 |
|--------|----------|-----------------|---------------|---------------|
| Fragment fields | 7 | 8 (+intent) | 8 | 8 |
| Gap scoring formula | count×quality×section | +intent_weight | +semantic | +graph |
| Discovery method | keyword only | +intent-aware | +hybrid embedding | +co-citation |
| DB tables | 7 | 7 | 7 | 8 (+citation_links) |
| DB schema version | 0 | 1 | 2 | 3 |
| Test files | 4 | 5 (+intent) | 6 (+embeddings) | 7 (+graph) |
| LOC | 8,387 | TBD | TBD | TBD |

---

# Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Prompt engineering instability (LLM output stochastic) | HIGH | temperature=0 for A/B tests, never backfill intent on existing data without user confirm |
| Scope creep from paper deadlines | MEDIUM-HIGH | Sprint 5 = stretch goals, +50% buffer on estimates |
| Fragment backward compatibility (old NULL intents) | MEDIUM | NULL intent → weight 1.0, regression tests on every scoring change |
| S2 API rate limits (100 req/5min) | MEDIUM | Throttling built into SemanticScholarEmbeddings from day 1 |
| Dimension mismatch between embedding backends | MEDIUM | Store embedding_model in DB, reject cross-model cosine |
| Sprint 2+3 merge conflicts in note_factory.py | LOW | Sprint 3 starts after Sprint 1 merge, coordinates with Sprint 2 on note_factory |
| MCP SDK API changes since removal | LOW | Write fresh, not restore; mcp as optional dep |

---

# Execution Order Summary

```
Pre: merge feature/outline-command → master, create baseline

Sprint 1 (Phase 0+1): ~5 days
  0.0 → 0.1 → 0.2 → 0.3 → 1.1 → 1.2 → 1.3
  PR → merge

Sprint 2 (Phase 2): ~8 days (after Sprint 1 merge)
  2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6
  PR → merge

Sprint 3 (Phase 3): ~5 days (after Sprint 1 merge, can overlap Sprint 2 end)
  3.1 → 3.2 → 3.3
  PR → merge

Sprint 4 (Phase 4): ~5 days (after Sprint 2)
  4.0 → 4.1 → 4.2
  PR → merge

Sprint 5 (Phase 5): ~7-10 days (stretch goals, after all)
  5.2 → 5.4 → 5.1 → 5.3
  PR → merge
```

**Total estimate**: ~30-38 days (including buffer for Sprint 5)
