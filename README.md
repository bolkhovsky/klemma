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

# 5. Просмотреть извлечённые фрагменты
klemma fragments

# 6. Открыть TUI-дашборд
klemma
```

## Команды

### `klemma`
Интерактивный TUI-дашборд (Textual). Показывает план на день, статистику, покрытие по главам.

Горячие клавиши: `d` — дашборд, `f` — фрагменты, `r` — обновить, `q` — выход.

### `klemma prepopulate`
Импортирует заметки `@*.md` из Obsidian vault в базу данных. Читает YAML-фронтматтер каждой заметки (quality, priority, chapter, section, relevance_nr1/nr2) и регистрирует источник.

```bash
klemma prepopulate                # импортировать все источники
klemma prepopulate --with-queue   # + добавить high-priority в очередь чтения
```

### `klemma morning`
Генерирует ежедневный план через Claude: задача для диссертации, задача для ассистента, рекомендация по чтению. Учитывает вчерашний план, покрытие глав, пробелы в источниках. План сохраняется в базу и дописывается в daily note Obsidian.

### `klemma extract <citekey>`
Извлекает фрагменты для цитирования из PDF. Находит PDF в хранилище Zotero, извлекает текст (PyMuPDF), отправляет в Claude для анализа. Каждый фрагмент маппится на главу/раздел диссертации с оценкой релевантности.

```bash
klemma extract anderssonSeasonalArcticSea2021
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
chapter: 2
section: "2.3.1"
relevance_nr1: 5
relevance_nr2: 4
tags: ["Sea-Ice", "Machine-Learning"]
---
```

## Архитектура

```
klemma (CLI/TUI)
├── Claude Code CLI ── AI-анализ (планирование, извлечение фрагментов)
├── Obsidian vault ─── заметки источников + daily notes
├── Zotero storage ─── PDF файлы
├── PyMuPDF ────────── извлечение текста из PDF
└── SQLite ─────────── состояние: sources, fragments, daily_plans, reading_queue
```
