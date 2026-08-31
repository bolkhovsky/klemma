---
name: klemma-query
description: Read-only SQL queries to klemma databases for ad-hoc analytics — coverage slices, fragment stats, claim-audit pulls, gap/prune reports. Use for one-off analytical questions about the library instead of inventing new CLI flags. Mutations always go through klemma CLI.
allowed-tools: Bash(python3:*), Grep, Read
---

# klemma-query — read-only запросы к данным klemma

Разовые аналитические вопросы к библиотеке решаются inline-скриптом на stdlib
`sqlite3`, а не новым флагом в CLI. Паттерн: **CLI для записи, прямой SQL для
чтения**.

## Граница (жёстко)

- **ТОЛЬКО чтение.** Каждое соединение — строго read-only URI:
  ```python
  conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
  ```
- Любые мутации (добавить источник, извлечь фрагменты, вердикты) — через CLI
  klemma на fram (`klemma acquire/process/...`), никогда inline-скриптом.
- **Не импортировать Python API klemma**: `StateManager.__init__` не
  read-safe — делает mkdir, `executescript(SCHEMA)`, миграции, WAL-коммиты.
- Виртуальные таблицы `fragments_vec_*` не трогать: без модуля vec0 любой
  SELECT по ним падает `no such module: vec0`. Перечисление таблиц оборачивай
  в try/except.
- `~/.klemma/jobs.db` и `~/.klemma/users.db` — служебные SaaS-базы, не трогать.

## Карта данных: три БД (ADR-014)

Целевые базы — **локальные на маке**. Предупреждение: klemma выполняется на
fram, локальные копии могут отставать от последних `acquire` (известное
расхождение: легаси-БД мак 443 vs fram 401 источник).

### 1. `~/.klemma/library.db` — глобальная библиотека (мультипользовательская)

| Таблица | Ключевые колонки |
|---|---|
| `papers` | paper_id, pdf_hash, doi, title, authors, year, abstract, raw_text |
| `fragments` | fragment_id, paper_id, extraction_id, fragment_text, fragment_type (key_idea\|result\|methodology\|definition\|conclusion\|quote + редкие result_comparison\|dataset\|limitation), page_number, citation_intent, verbatim |
| `extractions` | extraction_id, paper_id, ai_model, fragment_count, extracted_at |
| `user_sources` | citekey, paper_id, **status**, pdf_path, note_path, quality_score, **user_id** |
| `user_source_sections` / `user_source_chapters` | citekey × section/chapter × user_id |

⚠️ **Скоупинг по user_id обязателен**: диссертация = `user_id = ''`
(432 источника). Кроме неё в базе тестовые фикстуры `test-user-123`
(26 источников; в том числе ВСЕ 19 фрагментов с verbatim=1 — «Quoted Paper»)
и 2 источника другого пользователя. Глобальные totals ≠ диссертационные.
В диссертационном скоупе verbatim=1 фрагментов **ноль** (0 из 3866) — дословные
формулировки берутся только из `.klemma/pdfs/<citekey>.md`, не из фрагментов.

### 2. `<проект>/.klemma/data/project.db` — проектные привязки

Для диссертации: `/Users/ilya/research/dissertation/.klemma/data/project.db`.

| Таблица | Ключевые колонки |
|---|---|
| `project_sources` | citekey, paper_id, primary_chapter, primary_section, relevance_nr1/nr2, citation_priority, user_id — **без status** |
| `project_fragments` | fragment_id, citekey, chapter, section, section_type, relevance_score, used_in_draft |
| `project_source_sections` | citekey × section × chapter (many-to-many) |
| `prune_verdicts` | source_id, verdict (drop\|maybe), updated_at — **drop действует 14 дней** |

### 3. Легаси `<проект>/.klemma/data/klemma.db` — монолитный снапшот

Единственное место, где есть `reference_gaps` (status open/resolved,
dissertation_sections — **JSON-текст**, парсить `json_each()`, не LIKE),
`citation_links`, `decisions`, `section_type_map`. Остальные таблицы
(sources 443, fragments 2450) — исторический снапшот, для актуальных цифр
использовать library.db + project.db.

### Кросс-БД связки

- Внутри library: `fragments.paper_id → papers.paper_id`.
- Между базами мост — ВСЕГДА по паре `(citekey, user_id)`:
  `u.citekey = ps.citekey AND u.user_id = ps.user_id`.
- Join между файлами: `conn.execute("ATTACH 'file:...?mode=ro' AS lib")`.
- Известные дыры (учитывать явными корзинами): 20 из 436 project_sources
  отсутствуют в user_sources диссертационного скоупа (LEFT JOIN + корзина
  «orphan»); у 27 project_sources `primary_chapter IS NULL` (корзина
  «UNASSIGNED» — иначе суммы не сойдутся с 436).

### Семантика покрытия (не изобретать свою)

- По главам — счёт источников по `primary_chapter`.
- По разделам — `COUNT(DISTINCT citekey)` через `project_source_sections`;
  join без DISTINCT даёт двойной счёт.

## Шаблон запуска

```bash
python3 - <<'PY'
import sqlite3
LIB = "/Users/ilya/.klemma/library.db"
PROJ = "/Users/ilya/research/dissertation/.klemma/data/project.db"
LEGACY = "/Users/ilya/research/dissertation/.klemma/data/klemma.db"

conn = sqlite3.connect(f"file:{PROJ}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
conn.execute(f"ATTACH 'file:{LIB}?mode=ro' AS lib")
# ... запрос ...
PY
```

## Примеры

### 1. Покрытие по главам со статусами

```python
rows = conn.execute("""
  SELECT COALESCE(CAST(ps.primary_chapter AS TEXT), 'UNASSIGNED') AS chapter,
         COUNT(*) AS sources,
         SUM(CASE WHEN u.status = 'completed' THEN 1 ELSE 0 END) AS completed,
         SUM(CASE WHEN u.citekey IS NULL THEN 1 ELSE 0 END) AS orphan
  FROM project_sources ps
  LEFT JOIN lib.user_sources u
         ON u.citekey = ps.citekey AND u.user_id = ps.user_id
  GROUP BY 1 ORDER BY 1
""").fetchall()
```

### 2. Фрагменты диссертации: типы, страницы, verbatim

```python
row = conn.execute("""
  SELECT COUNT(*) AS total,
         SUM(f.verbatim) AS verbatim,
         SUM(f.page_number IS NOT NULL) AS with_page
  FROM lib.fragments f
  JOIN lib.user_sources u ON u.paper_id = f.paper_id AND u.user_id = ''
""").fetchone()
by_type = conn.execute("""
  SELECT f.fragment_type, COUNT(*) c
  FROM lib.fragments f
  JOIN lib.user_sources u ON u.paper_id = f.paper_id AND u.user_id = ''
  GROUP BY 1 ORDER BY c DESC
""").fetchall()
```

### 3. Выборка под claim audit: все фрагменты источника

```python
citekey = "goesslingPredictabilitySeaIce2016"
rows = conn.execute("""
  SELECT f.fragment_type, f.page_number, f.verbatim, f.citation_intent,
         substr(f.fragment_text, 1, 120) AS preview
  FROM lib.user_sources u
  JOIN lib.fragments f ON f.paper_id = u.paper_id
  WHERE u.citekey = ? AND u.user_id = ''
  ORDER BY f.page_number
""", (citekey,)).fetchall()
```

### 4. Открытые reference_gaps по разделу + действующие prune-вердикты

```python
leg = sqlite3.connect(f"file:{LEGACY}?mode=ro", uri=True)
gaps = leg.execute("""
  SELECT g.ref_authors, g.ref_year, g.ref_title, je.value AS section
  FROM reference_gaps g, json_each(g.dissertation_sections) je
  WHERE g.status = 'open' AND je.value = ?
""", ("2.3",)).fetchall()
prune = conn.execute("""
  SELECT source_id, verdict, reason FROM prune_verdicts
  WHERE updated_at >= datetime('now', '-14 days')
""").fetchall()
```

## Vault как дополнение

`/Users/ilya/Documents/Obsidian Vault/2 - Refs/` — 417 заметок `@citekey.md`.
Идти туда (через Grep/Read, НЕ через VaultAdapter) только за тем, чего нет в
БД: теги/topics во frontmatter и полный человекочитаемый текст цитат.
`note_path` в user_sources неоднороден (264 относительных, 157 абсолютных,
22 пустых): относительный путь резолвить от корня vault.

## Эскалация

Если запрос прижился и повторяется — не копипастить: оформить модулем в
`tools/` klemma или предложить пользователю команду в CLI.
