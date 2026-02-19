<div align="center">

```
    /\  /\
   ( o  o )   Klemma
   (  >>  )   AI Academic Assistant
    / || \
   (_/  \_)
```

# Klemma

</div>

AI-ассистент для работы над диссертацией. Управляет библиотекой источников (Zotero), извлекает цитируемые фрагменты из PDF (Claude), генерирует ежедневные планы, исследовательские брифинги, анализ библиотеки и отслеживает покрытие глав диссертации.

## Установка

```bash
cd ~/projects/klemma
pip install -e .
```

Требуется:
- Python 3.11+
- Claude Code CLI (`claude` в PATH)
- Obsidian vault с заметками источников

## Быстрый старт

```bash
# 1. Открыть TUI-дашборд
klemma

# 2. Сгенерировать план на день (включает дайджест библиотеки)
klemma plan

# 3. Посмотреть статистику, покрытие, пробелы
klemma status              # компактный обзор
klemma status --verbose    # полные таблицы
klemma status --chapter 2  # фильтр по главе

# 4. Обработать источник (фрагменты + аннотация + vault note)
klemma process anderssonSeasonalArcticSea2021   # один источник
klemma process                                  # все pending

# 5. Исследовательский брифинг по разделу
klemma research -s 1.3.2

# 6. AI-анализ библиотеки
klemma library             # здоровье библиотеки
klemma library -s 2.3      # рекомендации для раздела
klemma library --audit     # глубокий аудит качества

# 7. Задать вопрос агенту с полным контекстом диссертации
klemma ask "Какие основные методы валидации прогнозов ледовой обстановки?"

# 8. Управление MCP-серверами (Zotero, arXiv, ...)
klemma tools add zotero --command "uvx" --args "zotero-mcp" --env ZOTERO_LOCAL=true
klemma tools list --probe     # подключиться и показать доступные инструменты

# 9. Поиск статей через MCP (arXiv, Semantic Scholar)
klemma tools add academia --command "python3" --args "-m academia_mcp --transport stdio"
klemma search "AMSR2 sea ice forecast validation"

# 10. Автоматический поиск новой литературы для раздела
klemma discover -s 1.3.2              # запустить discovery pipeline
klemma discover --status              # статус
klemma discover --review              # просмотр найденного
```

## Команды (10)

### `klemma`
Интерактивный TUI-дашборд (Textual). Показывает план на день, статистику, покрытие по главам, пробелы, reference gaps.

Горячие клавиши: `d` — дашборд, `f` — фрагменты, `r` — обновить, `q` — выход.

### `klemma plan`
Генерирует ежедневный план через Claude: фокус дня, рекомендации по чтению, задача для ассистента, стратегические предложения. Включает дайджест библиотеки. Учитывает вчерашний план, покрытие глав, пробелы, дедлайны. План сохраняется в базу и дописывается в daily note Obsidian.

### `klemma status`
Единая команда для статистики, покрытия и пробелов. Показывает: количество обработанных/pending/failed источников, покрытие по главам, разделы с недостаточным покрытием, reference gaps (ссылки из библиографий источников, отсутствующие в нашей библиотеке).

```bash
klemma status              # компактный обзор
klemma status --verbose    # полные таблицы + детализация
klemma status --chapter 2  # фильтр по конкретной главе
```

### `klemma process [<citekey>]`
Полный пайплайн обработки источника: поиск PDF → извлечение текста (PyMuPDF) → AI-анализ → сохранение фрагментов в SQLite + vault-заметку.

**С аргументом** — обрабатывает один указанный источник.
**Без аргумента** — batch-режим: обрабатывает все pending источники.

При обработке автоматически:
- Создаёт vault-заметку `@citekey.md`, если она отсутствует (AI-аннотация: summary, методология, релевантность, key references)
- Извлекает фрагменты для цитирования и маппит их на главы/разделы диссертации
- Анализирует библиографию источника и записывает reference gaps (ссылки, отсутствующие в нашей библиотеке)
- Авто-резолвит ранее найденные reference gaps, если соответствующие источники уже добавлены

```bash
klemma process anderssonSeasonalArcticSea2021   # один источник
klemma process                                  # все pending
```

### `klemma research -s <X.X>`
Исследовательский брифинг: глубокий анализ готовности раздела к написанию. Автоматически извлекает фрагменты для всех источников раздела (если ещё не извлечены), собирает контекст (черновик, план сессий, фрагменты, аннотации, покрытие) и генерирует структуру аргументации с планом цитирования.

При повторном запуске работает в инкрементальном режиме: читает заметки пользователя из `## ✏️ Что нового`, определяет дельту (новые источники и фрагменты) и обновляет брифинг. Заметки пользователя архивируются в `## 📋 История изменений` с таймстампом.

```bash
klemma research -s 1.3.2            # первый запуск: полный анализ
klemma research -s 1.3.2            # повторный: инкрементальное обновление
klemma research -s 1.3.2 --force    # переизвлечь все фрагменты
klemma research -s 1.3.2 --enrich   # добавить свежие статьи через MCP (если настроен)
```

### `klemma library [-s <X.X>] [--audit]`
AI-анализ библиотеки. Три режима:

- **status** (по умолчанию) — общее здоровье библиотеки: покрытие по главам, оценка качества, критические проблемы
- **recommend** (`-s 2.3`) — рекомендации по чтению для конкретного раздела: порядок чтения, оценка имеющихся источников
- **audit** (`--audit`) — глубокий аудит качества: дублирование, устаревшие источники, пробелы в методологии

Отчёт сохраняется в vault (`Library/Library_{mode}_{date}.md`).

```bash
klemma library              # здоровье библиотеки
klemma library -s 2.3       # рекомендации для раздела 2.3
klemma library --audit      # глубокий аудит
```

### `klemma ask "query"`
Универсальный исследовательский агент. Запускает Claude Code в интерактивном режиме с полным контекстом диссертации: структура, источники, покрытие, пробелы, статистика фрагментов, план дня, очередь чтения. Claude получает доступ к инструментам (веб-поиск, файлы, bash) и сохраняет ответ в vault (`Agent/Agent_<date>.md`).

```bash
klemma ask "Какие основные методы валидации прогнозов ледовой обстановки?"
klemma ask -s 1.3.2 "Найди статьи об AMSR2"
klemma ask -ch 2 "Сравни архитектуры IceNet и ConvLSTM"
```

### `klemma tools {add,list,remove,call}`
Управление MCP-серверами. Klemma использует протокол [MCP](https://modelcontextprotocol.io) для подключения внешних инструментов (Zotero, arXiv, Semantic Scholar и др.).

- **add** — зарегистрировать MCP-сервер (пишет в `config.yaml → mcp.servers`)
- **list** — показать зарегистрированные серверы; `--probe` подключается и показывает доступные tools
- **remove** — удалить сервер из конфига
- **call** — прямой вызов инструмента (debug/power user)

```bash
klemma tools add zotero --command "uvx" --args "zotero-mcp" --env ZOTERO_LOCAL=true
klemma tools add academia --command "python3" --args "-m academia_mcp --transport stdio"
klemma tools list --probe
klemma tools call zotero zotero_search_items '{"query": "ice forecast"}'
klemma tools remove academia
```

### `klemma search "query"`
Поиск статей через MCP-серверы (arXiv и др.). Требует зарегистрированный `academia` сервер. Результаты выводятся в таблице; можно добавить найденные в библиотеку.

```bash
klemma search "AMSR2 sea ice forecast"
klemma search "neural ice prediction" --limit 10
```

### `klemma discover -s <X.X>`
Hybrid discovery pipeline: автоматический поиск новых статей для раздела. Работает в два этапа:
1. **Phase 1** (детерминированный): MCP-поиск по open reference gaps и ключевым словам раздела
2. **Phase 2** (Claude): оценка релевантности найденного (relevance 1-5, usage type, priority)

Результаты сохраняются в таблицу `discoveries` в SQLite; просмотр через `--review`.

```bash
klemma discover -s 1.3.2                # запуск pipeline
klemma discover -s 1.3.2 --background   # запуск в фоне
klemma discover --status                # статус фоновых процессов
klemma discover --review                # просмотр и принятие/отклонение найденного
```

### Backward-compatible aliases

Старые имена команд работают как скрытые алиасы: `morning`→`plan`, `extract`→`process`, `agent`→`ask`, `stats`/`coverage`/`gaps`→`status`, `prepopulate`→`import`.

## Конфигурация

Файл `config.yaml` в корне проекта:

```yaml
ai:
  model: "opus"              # модель Claude Code CLI (sonnet/opus)
  max_pdf_chars: 50000       # максимум символов из PDF
  timeout: 180               # таймаут на вызов Claude (сек)

obsidian:
  vault_path: "/path/to/vault"
  notes_folder: "2 - Refs"   # папка с заметками источников
  tags_folder: "3 - Tags"

zotero:
  library_json: "/path/to/pubs-bibtex.json"   # BetterBibTeX auto-export (для PDF lookup)
  backend: "local"                             # "local" (default) | "mcp"

mcp:                                           # MCP-серверы (управляются через klemma tools)
  servers:
    zotero:
      command: "uvx"
      args: ["zotero-mcp"]
      env:
        ZOTERO_LOCAL: "true"
    academia:
      command: "python3"
      args: ["-m", "academia_mcp", "--transport", "stdio"]

state:
  db_path: "./data/klemma.db" # путь к SQLite базе

dissertation:
  current_chapter: 2
  current_section: "2.3.1"
  chapters:
    1: "Analysis of ice forecasting domain"
    2: "Geoinformation validation model"
    3: "Validation methodology"
    4: "Algorithm & software implementation"
  chapter_mapping:            # regex → глава/раздел
    - pattern: "icenet|ice.?net"
      chapter: 2
      section: "2.3.1"
  min_sources_per_section: 3
```

`zotero.library_json` — путь к BetterBibTeX JSON-экспорту. Используется для надёжного нахождения PDF по citekey. PDF ищутся в 3 этапа: прямой путь из БД → BetterBibTeX lookup (citekey → attachment path) → нечёткий поиск по имени файла в Zotero storage.

## Формат заметок Obsidian

Klemma читает YAML-фронтматтер заметок `@citekey.md`:

```yaml
---
citekey: "anderssonSeasonalArcticSea2021"
title: "Seasonal Arctic sea ice forecasting..."
author: "Tom R. Andersson..."
year: 2021
quality: 5
priority: "high"
chapter: 2                                    # основная глава
section: "2.3.1"                              # основной раздел
sections: [1.4.3, 2.2.2, 2.3.1, 3.2.1]      # все релевантные разделы
chapters: [1, 2, 3]                           # все релевантные главы
relevance_nr1: 5
relevance_nr2: 4
tags: ["Sea-Ice", "Machine-Learning"]
---
```

`chapter`/`section` — основной раздел (primary). `sections`/`chapters` — все разделы, в которых источник полезен. `klemma process` создаёт vault-заметки автоматически при обработке; `klemma research` находит источники по всем секциям.

## Архитектура

```
klemma (CLI/TUI)
├── Claude Code CLI ── AI-анализ
│   ├── планирование (plan)
│   ├── извлечение фрагментов (process)
│   ├── research briefing
│   ├── library analysis (status/recommend/audit)
│   ├── interactive agent (ask)
│   └── discovery assessment (discover phase 2)
├── MCP Tool Layer ─── plug-and-play внешние серверы
│   ├── ToolRegistry → MCPClient → stdio transport
│   ├── zotero-mcp ── Zotero library (search, metadata, fulltext)
│   └── academia-mcp ─ arXiv, Semantic Scholar, web search
├── LibraryProvider ── абстракция библиотеки (swappable backend)
│   ├── LocalLibrary ─ BBT JSON файл (default)
│   └── MCPLibrary ─── zotero-mcp server
├── Obsidian vault ─── заметки источников + research notes + daily notes + library reports
├── BetterBibTeX JSON ─ citekey → PDF path mapping
├── Zotero storage ─── PDF файлы (fallback)
├── PyMuPDF ────────── извлечение текста из PDF
└── SQLite
    ├── sources ─────────── Zotero entries, metadata, processing status
    ├── source_sections ─── junction: source × section (multi-section)
    ├── fragments ───────── citation fragments → chapter/section mapping
    ├── reference_gaps ──── missing refs from bibliographies (score, auto-resolve)
    ├── discoveries ─────── found papers (hybrid pipeline: MCP search + Claude assessment)
    ├── daily_plans ─────── generated plans
    └── reading_queue ───── prioritized reading list
```
