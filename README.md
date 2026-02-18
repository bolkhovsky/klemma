# Klemma

AI-ассистент для работы над диссертацией. Управляет библиотекой источников (Zotero), извлекает цитируемые фрагменты из PDF (Claude), генерирует ежедневные планы и отслеживает покрытие глав диссертации.

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
# 1. Импортировать существующие заметки из Obsidian в базу данных
klemma prepopulate

# 2. Посмотреть статистику и покрытие
klemma stats
klemma coverage
klemma gaps

# 3. Сгенерировать план на день
klemma morning

# 4. Извлечь фрагменты для цитирования из PDF источника
klemma extract anderssonSeasonalArcticSea2021

# 5. Исследовательский брифинг по разделу (авто-извлечение + анализ)
klemma research --section 1.3.2

# 6. Просмотреть извлечённые фрагменты
klemma fragments

# 7. Открыть TUI-дашборд
klemma
```

## Команды

### `klemma`
Интерактивный TUI-дашборд (Textual). Показывает план на день, статистику, покрытие по главам.

Горячие клавиши: `d` — дашборд, `f` — фрагменты, `r` — обновить, `q` — выход.

### `klemma prepopulate`
Импортирует заметки `@*.md` из Obsidian vault в базу данных. Читает YAML-фронтматтер каждой заметки (quality, priority, chapter, section, sections, chapters, relevance_nr1/nr2) и регистрирует источник.

Поддерживает мульти-секционные источники: `sections: [1.1, 1.4.1, 3.2.2]` → заполняет таблицу `source_sections` для поиска по всем разделам.

```bash
klemma prepopulate                # импортировать все источники
klemma prepopulate --with-queue   # + добавить high-priority в очередь чтения
```

### `klemma morning`
Генерирует ежедневный план через Claude: задача для диссертации, задача для ассистента, рекомендация по чтению. Учитывает вчерашний план, покрытие глав, пробелы в источниках. План сохраняется в базу и дописывается в daily note Obsidian.

### `klemma extract <citekey>`
Извлекает фрагменты для цитирования из PDF. Находит PDF через BetterBibTeX JSON lookup или в хранилище Zotero, извлекает текст (PyMuPDF), отправляет в Claude для анализа. Каждый фрагмент маппится на главу/раздел диссертации с оценкой релевантности. Фрагменты сохраняются в SQLite и в vault-заметку `@citekey.md` (секция `## 💬 Цитаты для диссертации`).

```bash
klemma extract anderssonSeasonalArcticSea2021
```

### `klemma research --section <X.X>`
Исследовательский брифинг: анализ готовности раздела к написанию. Автоматически извлекает фрагменты для всех источников раздела (если ещё не извлечены), собирает контекст (черновик, план сессий, фрагменты, аннотации, покрытие) и генерирует структуру аргументации с планом цитирования.

При повторном запуске работает в инкрементальном режиме: читает заметки пользователя из `## ✏️ Что нового`, определяет дельту (новые источники и фрагменты) и обновляет брифинг. Заметки пользователя архивируются в `## 📋 История изменений` с таймстампом.

```bash
klemma research --section 1.3.2          # первый запуск: полный анализ
klemma research --section 1.3.2          # повторный: инкрементальное обновление
klemma research --section 1.3.2 --force  # переизвлечь все фрагменты
```

### `klemma stats`
Статистика обработки: сколько источников pending/completed/failed/skipped. Количество фрагментов по типам.

### `klemma coverage`
Покрытие диссертации по главам и разделам — сколько источников привязано к каждому разделу.

### `klemma gaps`
Разделы с недостаточным покрытием (менее N источников).

```bash
klemma gaps              # порог по умолчанию: 3
klemma gaps -m 5         # минимум 5 источников на раздел
```

### `klemma fragments`
Просмотр извлечённых фрагментов с фильтрацией.

```bash
klemma fragments                  # все фрагменты
klemma fragments -ch 2            # только глава 2
klemma fragments -s 2.3.1         # только раздел 2.3.1
klemma fragments -t methodology   # только методология
```

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

`chapter`/`section` — основной раздел (primary). `sections`/`chapters` — все разделы, в которых источник полезен. `klemma prepopulate` записывает оба варианта; `klemma research` находит источники по всем секциям.

## Конфигурация Zotero

Для надёжного нахождения PDF добавьте путь к BetterBibTeX JSON-экспорту:

```yaml
zotero:
  library_json: "/path/to/pubs-bibtex.json"   # BetterBibTeX auto-export
```

PDF ищутся в 3 этапа: прямой путь из БД → BetterBibTeX lookup (citekey → attachment path) → нечёткий поиск по имени файла в Zotero storage.

## Архитектура

```
klemma (CLI/TUI)
├── Claude Code CLI ── AI-анализ (планирование, фрагменты, research briefing)
├── Obsidian vault ─── заметки источников + research notes + daily notes
├── BetterBibTeX JSON ─ citekey → PDF path mapping
├── Zotero storage ─── PDF файлы (fallback)
├── PyMuPDF ────────── извлечение текста из PDF
└── SQLite ─────────── sources, source_sections, fragments, daily_plans, reading_queue
```
