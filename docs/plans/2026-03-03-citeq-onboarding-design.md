# CiteQ Onboarding: два режима инициализации проекта

> Design document. Реализация — при старте SaaS-спринта.

## Контекст

CiteQ SaaS требует лёгкий онбординг, который максимально быстро показывает ценность продукта. Два типичных сценария пользователя:
- **«С нуля»** — есть тема, ключевые слова, может 1-2 статьи. Нужно помочь собрать начальную библиотеку.
- **«Из черновика»** — есть PDF/DOCX черновик. Нужно извлечь структуру, найти цитируемые источники, создать проект.

---

## Mode A: «С нуля» (Cold Start)

### Входные данные

```python
TopicSeedInput:
  title: str                    # тема
  description: str              # 1-3 предложения
  keywords: list[str]           # ключевые слова
  language: str                 # ru/en
  project_type: str             # paper/dissertation/thesis
  seed_dois: list[str]          # 0-5 стартовых DOI (опционально)
```

### Пайплайн (4 фазы)

```
Ввод темы → Поиск статей → Отбор пользователем → Генерация outline
           (SearchProvider)  (callback / UI)      (outliner.py)

Фаза 1: SearchProvider.search(keywords, description) → 50 кандидатов
         + seed_dois → SearchProvider.get_by_doi() → добавить в начало
Фаза 2: AI scoring (prompts/onboarding_score.md) → CandidatePaper с relevance
Фаза 3: Пользователь выбирает (CLI: numbered list / SaaS: карточки)
         Или --auto-top 15 для non-interactive
Фаза 4: register_sources() → generate_outline() → проект готов
```

### CLI

```bash
# Интерактивный
klemma init --from-scratch

# Non-interactive (для SaaS/скриптов)
klemma init --from-scratch --name "Ice dynamics" \
  --keywords "ice sheets,climate" --auto-top 15
```

### SaaS Flow

```
POST /api/v1/onboarding/scratch        → session_id
GET  /api/v1/onboarding/{id}/progress  → SSE stream
GET  /api/v1/onboarding/{id}/candidates → карточки статей
POST /api/v1/onboarding/{id}/select     → выбранные ID → resume pipeline
```

---

## Mode B: «Из черновика» (Warm Start)

### Входные данные

Файл: PDF или DOCX (`python-docx` — новая зависимость, опциональная).

### Пайплайн (5 фаз)

```
Upload файла → Парсинг → AI анализ → Резолв ссылок → Проект готов
              (DraftParser)         (SearchProvider.resolve)

Фаза 1: DraftParser.parse(path) → DraftParseResult
           - full_text, detected_sections[], raw_references[]
           - metadata (title, authors, language)
Фаза 2: AI (prompts/onboarding_structure.md) → refined outline + metadata
           - нормализация секций, заполнение пробелов
           - оценка зрелости по секциям (% готовности)
Фаза 3: Для каждой raw_reference: SearchProvider.resolve(ref)
           DOI lookup → title fuzzy match → None
Фаза 4: AI (prompts/onboarding_cite_map.md) → section ↔ reference mapping
           + citation_intent (background/method/result_comparison)
Фаза 5: init_project() + register_sources() + save outline
           Неразрешённые ссылки → reference_gaps
```

### CLI

```bash
klemma init --from-draft ./paper.pdf
klemma init --from-draft ./paper.docx --auto   # без подтверждений
```

### Парсеры

**PDF** (PyMuPDF — уже в зависимостях):
- `get_text("dict")` → font-size анализ → иерархия заголовков
- Библиография: детект секции «Литература»/«References» → парсинг записей
- In-text citations: regex для `[1]`, `(Author, Year)` стилей

**DOCX** (`python-docx` — новая опциональная зависимость):
- `paragraph.style.name` → Heading 1/2/3 → структура
- Каждый параграф после «Список литературы» → одна ссылка
- Metadata из `core_properties`

---

## Абстракции (Protocols)

### SearchProvider

```python
@runtime_checkable
class SearchProvider(Protocol):
    def search(self, query: str, keywords: list[str] | None = None,
               limit: int = 50) -> list[SearchResult]: ...
    def resolve(self, reference: RawReference) -> SearchResult | None: ...
    def get_by_doi(self, doi: str) -> SearchResult | None: ...
```

Реализации: `SemanticScholarSearch` (первый), `OpenAlexSearch` (позже).
Выбор через конфиг: `onboarding.search_backend: "s2" | "openalex"`.

### DraftParser

```python
@runtime_checkable
class DraftParser(Protocol):
    def parse(self, path: Path) -> DraftParseResult: ...
```

Dispatch по расширению: `.pdf` → `PDFDraftParser`, `.docx` → `DOCXDraftParser`.

---

## Модульная структура

```
src/klemma/onboarding/
  __init__.py              # exports: onboard_from_scratch, onboard_from_draft
  models.py                # TopicSeedInput, SearchResult, CandidatePaper,
                           # DraftParseResult, DetectedSection, RawReference, etc.
  search.py                # SearchProvider protocol + create_search_provider()
  search_s2.py             # Semantic Scholar (~150 строк)
  draft_parser.py          # DraftParser protocol + create_draft_parser()
  draft_pdf.py             # PDF парсинг (~200 строк)
  draft_docx.py            # DOCX парсинг (~150 строк)
  reference_parser.py      # Парсинг библиографических строк (~200 строк)
  scorer.py                # AI-скоринг релевантности (~100 строк)
  pipeline.py              # Оркестратор обоих пайплайнов

prompts/
  onboarding_score.md      # Оценка кандидатов для Mode A
  onboarding_structure.md  # Анализ черновика для Mode B
  onboarding_cite_map.md   # Маппинг ссылок → секции для Mode B
```

---

## Ключевые сигнатуры

```python
def onboard_from_scratch(
    seed: TopicSeedInput,
    project_dir: Path,
    search: SearchProvider,
    ai: AIProvider,
    config: KlemmaConfig,
    state: StateManager,
    auto_select_top: int = 0,
    on_progress: Callable[[OnboardingProgress], None] | None = None,
    on_candidates: Callable[[list[CandidatePaper]], list[CandidatePaper]] | None = None,
) -> ScratchOnboardingResult

def onboard_from_draft(
    draft_path: Path,
    project_dir: Path,
    search: SearchProvider,
    ai: AIProvider,
    config: KlemmaConfig,
    state: StateManager,
    on_progress: Callable[[OnboardingProgress], None] | None = None,
    on_references: Callable[[list[ResolvedReference]], list[ResolvedReference]] | None = None,
) -> DraftOnboardingResult
```

**Callback-паттерн** — мост между CLI и SaaS:
- CLI: `on_candidates = lambda c: rich_select(c)` (интерактивный выбор)
- SaaS: `on_candidates = lambda c: store_and_await_user(session_id, c)` (async через Redis/WS)

---

## Переиспользование существующего кода

| Модуль | Что переиспользуется |
|--------|---------------------|
| `setup.init_project()` + `InitValues` | Создание `.klemma/`, `KLEMMA.md` |
| `state.register_sources()` | Регистрация найденных/разрешённых статей |
| `state.update_source_info()` | Сохранение metadata (title, authors, year, abstract) |
| `state.set_source_sections()` | Привязка ссылок к секциям (Mode B) |
| `state.save_reference_gaps()` | Неразрешённые ссылки → gaps (Mode B) |
| `skills/outliner.generate_outline()` | Генерация outline в конце обоих режимов |
| `literature/metadata.lookup_s2()` | Fuzzy title matching для `resolve()` |
| `literature/pdf.PDFExtractor` | Базовое извлечение текста из PDF |
| `discovery.discover_relevant_sources()` | Fallback при отсутствии SearchProvider (offline CLI) |

**Модификации существующих модулей не требуются** — дизайн аддитивный.

---

## Конвергенция состояний

Оба режима приводят проект к одинаковому состоянию:

```
Любой путь → PROJECT_INITIALIZED → LIBRARY_SEEDED → OUTLINE_GENERATED
             (.klemma/ + KLEMMA.md)  (N sources в DB)  (Outline_{name}.md)
```

После онбординга все команды работают: `klemma status`, `klemma research`, `klemma library`.

---

## Открытые вопросы (решить при имплементации)

1. **S2 rate limit**: 100 req/5min без ключа. Для SaaS потребуется ключ или кэширование.
2. **PDF bibliography parsing**: формат сильно варьируется. MVP: best-effort + AI fallback.
3. **Большие черновики** (>100 стр): полный текст для парсинга структуры, усечённый для AI промптов.
4. **`.doc` (legacy)**: `python-docx` не поддерживает. MVP: только `.docx`, ошибка для `.doc`.
5. **Offline mode** (CLI): если нет интернета, fallback на локальный Zotero/BBT JSON через `discover_relevant_sources()`.
6. **Session persistence** (SaaS): пайплайн разбит на фазы с сериализуемым промежуточным состоянием для pause/resume.

---

## Порядок реализации

1. `models.py` — все dataclass'ы (0 зависимостей)
2. `search.py` + `search_s2.py` — SearchProvider + S2 (тестируемо изолированно)
3. `reference_parser.py` — парсинг библиографии (чистый string processing)
4. `draft_pdf.py` — PDF парсинг (PyMuPDF)
5. `draft_docx.py` — DOCX парсинг (`python-docx`)
6. `scorer.py` — AI скоринг + промпт
7. Промпты — 3 шаблона (`onboarding_score.md`, `onboarding_structure.md`, `onboarding_cite_map.md`)
8. `pipeline.py` — оркестратор
9. CLI — `--from-scratch` / `--from-draft` в `klemma init`
10. Тесты — unit для парсеров, integration для pipeline с мокнутым SearchProvider/AI
