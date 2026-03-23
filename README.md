<div align="center">

```
    /\  /\
   ( o  o )   Klemma
   ( ^_^  )   AI Academic Assistant
    \    /
   (_/  \_)
```

# Klemma

</div>

AI-ассистент для академического письма. Управляет библиотекой источников (Zotero), извлекает цитируемые фрагменты из PDF (AI — Claude, OpenAI, Ollama, LiteLLM), классифицирует citation intent, генерирует ежедневные планы, исследовательские брифинги, анализ библиотеки, семантический поиск похожих источников и отслеживает покрытие глав. Fragment RAG обеспечивает ответы, подкреплённые реальными цитатами из библиотеки. Поддерживает вложенные проекты (диссертация + статьи) с раздельными базами и наследованием ресурсов.

## Установка

```bash
cd ~/projects/klemma
pip install -e .
```

Требуется:
- Python 3.11+
- AI-бэкенд (один из):
  - `pip install klemma[recommended]` — LiteLLM: 100+ провайдеров (рекомендуется)
  - `pip install klemma[openai]` — OpenAI API / Ollama / vLLM / LM Studio
  - Claude Code CLI (`claude` в PATH) — без дополнительных пакетов
- Obsidian vault с заметками источников
- Zotero с BetterBibTeX plugin (JSON auto-export)

### Опциональные зависимости

```bash
pip install klemma[recommended]        # LiteLLM — рекомендуемый AI-бэкенд
pip install klemma[embeddings]         # семантический поиск (S2/OpenAI бэкенды)
pip install klemma[local-embeddings]   # офлайн SPECTER2 (sentence-transformers)
pip install klemma[mcp]                # MCP-серверы (расширяемость)
pip install klemma[all-ai]             # все AI-бэкенды (openai + litellm)
pip install klemma[api]                # SaaS REST API (FastAPI + uvicorn)
```

## Быстрый старт

```bash
# 1. Инициализировать проект
klemma init                                    # интерактивный мастер
klemma init --type paper                       # проект-статья
klemma init --outline                           # сгенерировать outline после init (нужен AI)

# 2. Посмотреть статистику, покрытие, пробелы
klemma status                                  # компактный обзор
klemma status --verbose                        # полные таблицы + intent matrix

# 3. Обработать источники (фрагменты + citation intent + vault note)
klemma process                                 # все pending (параллельно)
klemma process smithMachineLearning2020        # один источник

# 4. Сгенерировать embeddings для семантического поиска
klemma embed                                   # все sources с abstract
klemma similar smithMachineLearning2020        # похожие источники
klemma similar 2.3                             # похожие для раздела 2.3

# 5. Исследовательский брифинг по разделу
klemma research -s 1.3.2

# 6. AI-анализ библиотеки
klemma library                                 # здоровье
klemma library -s 2.3                          # рекомендации
klemma library --audit                         # аудит + citation graph

# 7. Задать вопрос агенту с контекстом проекта (Fragment RAG)
klemma ask "Какие методы валидации прогнозов ледовой обстановки?"

# 8. Структура проекта
klemma outline                                 # AI-генерация outline
klemma outline -p "Фокус на методологии"       # с директивой

# 9. Guided Serendipity — исследовательские развилки
klemma briefing goessling2016                  # анализ нового источника → развилки
klemma insights                                # слепые пятна + скрытые кластеры
klemma decisions trail                         # тропинка принятых решений
```

## Guided Serendipity

Методология AI-ассистированного исследования: система обнаруживает неожиданные связи в библиотеке и предлагает развилки, на которых исследователь принимает осознанные решения. Накопление решений формирует уникальную исследовательскую тропинку (Research Trail).

### `klemma briefing <citekey>`
Анализ нового источника: ключевые тезисы, связи с библиотекой, ниши, 2-3 развилки.

```bash
klemma briefing goessling2016              # briefing для одного источника
klemma briefing --pending                  # top-10 необработанных (по релевантности)
klemma briefing --pending -n 5             # top-5
```

### `klemma insights`
Анализ библиотеки без AI — чистый SQL + embeddings:

- **Слепые пятна** — разделы с количеством источников <50% от среднего
- **Скрытые кластеры** — семантически похожие источники из разных разделов

```bash
klemma insights                            # таблица + сохранение как decisions
```

### `klemma decisions`
Просмотр и управление исследовательскими решениями:

```bash
klemma decisions                           # список всех решений
klemma decisions --pending                 # только ожидающие ответа
klemma decisions show 5                    # детали решения #5
klemma decisions trail                     # хронологическая тропинка
klemma decide 5 B --reason "Ближе к IIEE" # выбрать вариант B
```

## GitHub PR Auto-Checking (Codex)

Репозиторий уже проверяется стандартным CI (`ruff` + `pytest`) на PR.
Чтобы добавить автоматические Codex-fix PR при падении CI:

1. Добавьте секрет репозитория: `OPENAI_API_KEY`.
2. Убедитесь, что в GitHub Actions включены permissions на запись для PR/contents.
3. Используйте workflow: `.github/workflows/codex-auto-fix.yml`.

Логика: при падении workflow `CI` Codex пытается сделать минимальный фикс, повторно запускает `ruff` и `pytest`, и открывает PR с исправлением.

## Команды (20)

### `klemma init`
Инициализация проекта в текущей директории. Создаёт `.klemma/` (config, tags, DB) и `KLEMMA.md` (контекст для AI). Интерактивный мастер обнаруживает Obsidian vault и Zotero автоматически.

```bash
klemma init                    # интерактивный мастер
klemma init --type paper       # проект-статья (вместо диссертации)
klemma init --no-input         # без вопросов (defaults)
klemma init --outline          # сгенерировать outline после init (нужен AI)
```

### `klemma plan`
Ежедневный план: фокус дня, рекомендации по чтению, задача для ассистента, стратегические предложения. Учитывает вчерашний план, покрытие глав, пробелы, дедлайны. План сохраняется в базу и daily note Obsidian.

### `klemma status`
Единая команда для статистики, покрытия и пробелов. Показывает: обработанные/pending/failed источники, покрытие по главам, разделы с недостаточным покрытием, reference gaps с intent-weighted scoring.

```bash
klemma status                  # компактный обзор
klemma status --verbose        # полные таблицы:
                               #   intent coverage matrix (background/method/result)
                               #   embedding stats
                               #   citation graph stats
klemma status --chapter 2      # фильтр по главе
```

### `klemma process [<citekeys>...]`
Полный пайплайн обработки: PDF → текст (PyMuPDF) → AI-анализ → SQLite + vault note.

При обработке автоматически:
- Создаёт vault-заметку `@citekey.md` (AI-аннотация: summary, методология, key references)
- Извлекает фрагменты с классификацией **citation intent** (background / method / result_comparison)
- Записывает reference gaps (ссылки из библиографий, отсутствующие в библиотеке)
- Строит citation graph (все ссылки в `citation_links`)
- Авто-генерирует embedding (если настроен embeddings backend)

```bash
klemma process                                 # batch: все pending (3 потока)
klemma process smithML2020 jonesNLP2019        # конкретные источники
klemma process --serial                        # последовательно (экономия API)
```

### `klemma embed [<citekey>]`
Генерация SPECTER/OpenAI embeddings для семантического поиска. Без аргумента — backfill для всех completed-источников с abstract.

```bash
klemma embed                                   # все без embeddings
klemma embed smithMachineLearning2020          # один источник
klemma embed --sections                        # centroid embeddings по разделам
klemma embed --dry-run                         # сколько будет обработано
klemma embed --backend local                   # override бэкенда
```

### `klemma similar <citekey|section>`
Семантический поиск похожих источников по embedding cosine similarity.

```bash
klemma similar smithML2020                     # похожие на этот источник
klemma similar 2.3                             # близкие к центроиду раздела 2.3
klemma similar smithML2020 -k 20               # top-20 результатов
```

При поиске по разделу показывает источники из **других** разделов, семантически близкие к данному — помогает обнаружить скрытые связи.

### `klemma research -s <X.X>`
Исследовательский брифинг: глубокий анализ готовности раздела к написанию. Автоматически извлекает фрагменты, собирает контекст (черновик, фрагменты, покрытие) и генерирует структуру аргументации с планом цитирования.

При повторном запуске — инкрементальный режим: читает заметки пользователя из `## ✏️ Что нового`, определяет дельту и обновляет брифинг.

Token-aware prompt budget (~20K токенов): автоматически сокращает контекст (draft → summaries → fragment text → source count → fragment count) при превышении лимита. Использует RAG-first поиск фрагментов через семантический embedding (fallback на section-based при <10 результатах).

```bash
klemma research -s 1.3.2                       # первый запуск: полный анализ
klemma research -s 1.3.2                       # повторный: инкрементальное обновление
klemma research -s 1.3.2 --force               # переизвлечь все фрагменты
```

### `klemma outline`
AI-генерация структуры проекта на основе файлов в директории, базы данных и KLEMMA.md. Инкрементальное обновление при повторном запуске.

```bash
klemma outline                                 # AI-генерация
klemma outline -p "Фокус на KG-подходах"       # с директивой
klemma outline --fresh                         # полная перегенерация
klemma outline --scan-only                     # только сканировать файлы
```

### `klemma library [-s <X.X>] [--audit]`
AI-анализ библиотеки. Три режима:

- **status** (по умолчанию) — здоровье: покрытие, качество, проблемы
- **recommend** (`-s 2.3`) — рекомендации по чтению для раздела
- **audit** (`--audit`) — глубокий аудит: дублирование, устаревшие источники, пробелы в методологии, **co-citation analysis**, **author network**, prune-рекомендации

```bash
klemma library                                 # здоровье
klemma library -s 2.3                          # рекомендации для раздела
klemma library --audit                         # глубокий аудит

klemma library prune                           # просмотр prune-рекомендаций
klemma library prune -v drop                   # только "drop" вердикты
klemma library prune --clear smithML2020       # очистить вердикт
```

### `klemma ask "query"`
Исследовательский агент с полным контекстом проекта и **Fragment RAG**: семантически ищет релевантные фрагменты из обработанных PDF и подставляет их в промпт. Ответы подкреплены реальными цитатами из библиотеки, а не общими знаниями модели. Без fragment embeddings — работает как раньше (metadata-only).

```bash
klemma ask "Какие основные методы валидации прогнозов?"
klemma ask -s 1.3.2 "Найди статьи об AMSR2"
klemma ask -ch 2 "Сравни архитектуры IceNet и ConvLSTM"
```

### `klemma acquire <url>`
Скачивание PDF и регистрация в базе. Для bulk-импорта — `--batch` с JSON-файлом.

```bash
klemma acquire https://arxiv.org/pdf/2101.12345.pdf
klemma acquire <url> --title "Paper" --authors "Smith, J." --year 2023
klemma acquire --batch papers.json             # массовый импорт
klemma acquire <url> --no-process              # не извлекать фрагменты
```

### `klemma suggest`
Поиск и рекомендация papers для заполнения reference gaps. Резолвит метаданные через CrossRef → S2 (chain), генерирует `klemma acquire` команды.

```bash
klemma suggest                         # top-10 gap suggestions
klemma suggest -n 20                   # больше результатов
klemma suggest -s 2.3                  # только для раздела 2.3
```

Фильтрация: публикации старше `suggest.max_age_years` (по умолчанию 10 лет) скрываются, кроме фундаментальных работ с высоким score (≥ `suggest.classic_min_score`). Настраивается в конфиге.

> **Backward compat**: `klemma gaps suggest` по-прежнему работает как скрытый alias.

### `klemma info`
Текущий проект: корневая директория, цепочка проектов, конфигурация, путь к БД.

### `klemma tree`
Дерево вложенных проектов от текущего корня.

### `klemma benchmark`
Фреймворк оценки качества: intent classification, gap ranking, embedding retrieval, citation reconstruction. Поддерживает историю запусков, сравнение, автоматический пайплайн и ablation-эксперименты.

```bash
klemma benchmark --export dataset.json          # шаблон датасета из БД
klemma benchmark -d dataset.json --metrics all  # все метрики
klemma benchmark -d dataset.json --semantic     # гибридный keyword × semantic
klemma benchmark --analyst smithML2020          # извлечь ground truth из PDF
klemma benchmark -d dataset.json --reconstruct  # citation reconstruction
klemma benchmark --candidates                   # кандидаты для бенчмарка
klemma benchmark --prepare smithML2020          # подготовить недостающие ссылки
klemma benchmark --auto                         # полный автономный пайплайн
klemma benchmark --history                      # история запусков
klemma benchmark --compare id1 id2              # сравнить два запуска

# Ablation-параметры
klemma benchmark --auto --temperature 0.5       # override температуры
klemma benchmark --auto --max-recs 3            # макс. рекомендаций на раздел
klemma benchmark --auto --fragments 10          # фрагментов на источник
klemma benchmark --auto --prompt-variant fewshot # few-shot промпт
```

### `klemma migrate [--dry-run]`
Миграция из старого формата (`~/.klemma/`) в per-directory проект. Разделяет конфиг на system (AI) и project (всё остальное), копирует context.md → KLEMMA.md.

### Backward-compatible aliases

Старые имена работают как скрытые алиасы: `morning`→`plan`, `extract`→`process`, `agent`→`ask`, `stats`/`coverage`/`gaps`→`status`, `prepopulate`→`import`.

## Конфигурация

Трёхуровневая: `~/.klemmarc.yaml` (глобальный) → `~/.klemma/config.yaml` (системный) → `.klemma/config.yaml` (проектный). Вложенные проекты наследуют `obsidian`, `zotero`, `ai`, `embeddings` от родителя.

### Глобальный конфиг (`~/.klemmarc.yaml`)

Создаётся автоматически при первом `klemma init` (permissions 0600). Содержит API-ключи и AI-настройки, общие для всех проектов.

```yaml
ai:
  backend: "litellm"           # "litellm" (default) | "claude" | "openai"
  model: "anthropic/claude-sonnet-4-20250514"  # provider/model формат
  timeout: 180                 # таймаут AI-вызова (сек)
  language: "ru"               # язык AI-ответов ("en", "ru", "de", ...)
  # json_mode: true            # structured JSON output (если бэкенд поддерживает)
  # base_url: "http://localhost:11434/v1"  # для Ollama/vLLM/LM Studio

api_keys:
  anthropic: "sk-ant-..."      # для anthropic/* моделей
  openai: "sk-..."             # для openai/* моделей
  # google: "..."              # для gemini/* моделей
```

### Системный конфиг (`~/.klemma/config.yaml`)

Альтернативное расположение для AI-настроек (legacy). Перекрывается `~/.klemmarc.yaml`.

```yaml
ai:
  backend: "litellm"           # "litellm" (default) | "claude" | "openai"
  model: "anthropic/claude-sonnet-4-20250514"
  timeout: 180
  language: "ru"
```

### Проектный конфиг (`.klemma/config.yaml`)

```yaml
obsidian:
  vault_path: "/path/to/vault"
  notes_folder: "2 - Refs"     # папка с заметками @citekey.md
  tags_folder: "3 - Tags"

zotero:
  library_json: "/path/to/bbt-export.json"   # BetterBibTeX JSON auto-export

embeddings:
  backend: "s2"                # "s2" (бесплатный S2 API) | "local" | "openai" | ""
  # model: "specter2"          # имя модели (зависит от бэкенда)
  # throttle: 3.1              # секунды между запросами к S2 API
  # api_key_env: "OPENAI_API_KEY"  # для OpenAI бэкенда

project:
  type: "dissertation"         # "dissertation" | "paper" | "thesis"
  title: "Название работы"
  chapters:
    1: "Литературный обзор"
    2: "Методология"
    3: "Результаты"
  chapter_mapping:             # regex → глава/раздел (auto-generated from chapter titles by `klemma outline`)
    - pattern: "icenet|ice.?net"
      chapter: 2
      section: "2.3.1"
  auto_register: mapped         # "mapped" = skip Zotero entries not matching chapter_mapping; "all" = ingest everything
  min_sources_per_section: 3

suggest:
  max_age_years: 10              # фильтровать papers старше N лет (0 = отключить)
  classic_min_score: 15.0        # не фильтровать фундаментальные работы с высоким score

state:
  db_path: "./data/klemma.db"
```

`zotero.library_json` — путь к BetterBibTeX JSON-экспорту. PDF ищутся в 3 этапа: прямой путь из БД → BetterBibTeX lookup (citekey → attachment path) → нечёткий поиск по имени файла в Zotero storage.

### Embedding-бэкенды

| Бэкенд | Размерность | Стоимость | Требования |
|--------|------------|-----------|------------|
| `s2` | 768 (SPECTER) | Бесплатно | Интернет, throttle 3.1с |
| `local` | 768 (SPECTER2) | Бесплатно | `klemma[local-embeddings]`, GPU рекомендуется |
| `openai` | 1536 | Платно | `klemma[openai]`, API key |

## Вложенные проекты

Klemma поддерживает Git/NPM-style вложенность. Каждый проект — отдельная БД, но vault и Zotero наследуются от родителя.

```
thesis_dir/
├── KLEMMA.md           # контекст диссертации
├── .klemma/            # БД диссертации
├── paper_ice/
│   ├── KLEMMA.md       # контекст статьи (AI видит оба)
│   └── .klemma/        # БД статьи (наследует vault/zotero)
└── paper_climate/
    ├── KLEMMA.md
    └── .klemma/
```

```bash
cd thesis_dir/paper_ice/
klemma status                  # БД статьи, vault диссертации
klemma info                    # показать цепочку проектов
klemma tree                    # дерево вложенности
```

## Формат заметок Obsidian

Klemma создаёт и читает заметки `@citekey.md` с YAML-фронтматтером:

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

`chapter`/`section` — primary. `sections`/`chapters` — все релевантные. `klemma process` создаёт заметки автоматически.

**Куда сохраняются отчёты**: AI-отчёты (`outline`, `research`, `library`) сохраняются в корень проекта (`project_root/`). Только заметки `@citekey.md` — в vault (`notes_folder`).

## Лицензия

Klemma — свободный инструмент для исследователей, но **не для коммерческого использования**.

- **Ядро** (`src/klemma/`) — [Polyform Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/). Свободно для академического, исследовательского и личного использования. Коммерческое использование запрещено.
- **SaaS** (`saas/`) — проприетарный код, все права защищены.

Для коммерческого лицензирования: ilya.bolkhovsky@gmail.com

## Архитектура

```
klemma (CLI, v0.4.1)
├── AI Provider ─────── AI-анализ (pluggable backend)
│   ├── LiteLLMClient ─ 100+ провайдеров (litellm SDK) — recommended, default
│   ├── ClaudeClient ── Claude Code CLI (claude -p)
│   └── OpenAIClient ── deprecated (делегирует в LiteLLM)
├── Error Taxonomy ──── KlemmaAIError (timeout/rate-limit/auth/response)
│   └── AICallResult ── timing, tokens, retries, model metadata
├── Embeddings ──────── семантический поиск (pluggable backend)
│   ├── SemanticScholar ─ S2 API (768-dim SPECTER, бесплатно)
│   ├── LocalSPECTER ──── sentence-transformers (офлайн)
│   └── OpenAI ─────────── text-embedding-3-small (1536-dim)
├── Fragment RAG ────── семантический поиск по фрагментам (ask, research)
├── LibraryProvider ── BBT JSON → citekey/PDF/metadata
├── Obsidian vault ─── @citekey.md + research notes + reports
├── BetterBibTeX JSON ─ citekey → PDF path mapping
├── Zotero storage ─── PDF файлы
├── PyMuPDF ────────── извлечение текста из PDF
├── Config ────────── ~/.klemmarc.yaml → ~/.klemma/ → .klemma/ (3-level merge)
└── SQLite (schema v13)
    ├── sources ─────────── записи Zotero (+ embedding BLOB, embedding_model)
    ├── source_sections ─── source × section (multi-section)
    ├── fragments ───────── фрагменты для цитирования (+ citation_intent, embedding, embedding_model)
    ├── reference_gaps ──── пробелы из библиографий (+ citation_intent, intent scoring)
    ├── citation_links ──── citation graph (source → target, intent, in_library)
    ├── decisions ───────── Guided Serendipity: развилки, выборы, Research Trail
    ├── daily_plans ─────── сгенерированные планы
    ├── reading_queue ───── очередь чтения
    ├── prune_verdicts ──── результаты аудита (drop/maybe)
    ├── benchmark_runs ──── история бенчмарков (metrics, config_snapshot, git_commit)
    └── section_embeddings ─ centroid embeddings разделов (section × model)
```
