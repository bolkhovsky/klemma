<div align="center">

# Klemma

**AI-ассистент для академического письма**

*Превращает PDF-библиотеку в аргументы — без галлюцинаций*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: Polyform NC](https://img.shields.io/badge/license-Polyform%20NC-orange.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Claude · GPT · Ollama](https://img.shields.io/badge/AI-Claude%20%C2%B7%20GPT%20%C2%B7%20Ollama-purple.svg)](#установка)

</div>

---

У тебя 200 источников в Zotero. Ты знаешь, что где-то читал нужную цитату, но не помнишь в какой статье и на какой странице. Klemma решает именно эту проблему.

```
$ klemma ask "Какие методы валидации прогнозов ледовой обстановки?"

→ Найдено 9 фрагментов из библиотеки

  [goessling2016] с.89  — "The IIEE metric decomposes spatial error into..."
  [melsom2019]    с.234 — "Spatial verification methods for sea ice..."
  [zampieri2019]  с.12  — "Threshold-based binary classification at 15%..."
```

Только твои источники. Никаких домыслов модели.

---

## Что умеет

**Fragment RAG** — при каждом ответе Klemma семантически ищет по фрагментам твоих PDF и цитирует с указанием страниц. Не общие знания модели, а конкретные строки из твоей библиотеки.

**Citation Intent** — каждый фрагмент классифицирован: `background` / `method` / `result_comparison`. Видно не только что написано в источнике, но и зачем он нужен.

**Guided Serendipity** — система обнаруживает неожиданные связи между источниками и предлагает развилки. Каждое решение фиксируется в Research Trail — твоя интеллектуальная история работы.

**Покрытие и пробелы** — `klemma status` показывает, каких типов источников не хватает по каждому разделу, и находит papers, которые стоит добавить.

**Работает с твоим стеком** — Zotero + BetterBibTeX, Obsidian vault, или просто папка с PDF. Данные хранятся локально в SQLite.

---

## Установка

```bash
pip install klemma
cd ~/my-dissertation
klemma init
```

Klemma обнаружит Zotero и Obsidian автоматически.

Для AI-бэкенда выбери один:

```bash
pip install klemma[recommended]   # LiteLLM — 100+ провайдеров (Claude, GPT, Gemini...)
pip install klemma[openai]        # OpenAI / Ollama / vLLM / LM Studio
```

Или используй Claude Code CLI — без дополнительных пакетов.

---

## Быстрый старт

```bash
# Обработать библиотеку (PDF → фрагменты → citation intent)
klemma process

# Задать вопрос с контекстом из источников
klemma ask "Сравни архитектуры IceNet и ConvLSTM"

# Исследовательский брифинг по разделу
klemma research -s 2.3

# Статус: покрытие, пробелы, reference gaps
klemma status

# Найти похожие источники
klemma similar goessling2016
```

---

## Вложенные проекты

Диссертация + несколько статей — каждый проект отдельная база, но Zotero и vault общие:

```
dissertation/
├── KLEMMA.md           ← контекст для AI
├── .klemma/            ← база диссертации
├── paper_ice/
│   └── .klemma/        ← база статьи (наследует vault и Zotero)
└── paper_climate/
    └── .klemma/
```

---

## Облачная синхронизация

```bash
pip install klemma-cli

klemma-cli link    # подключить к litresearch.ru
klemma-cli push    # синхронизировать библиотеку и черновики
klemma-cli pull    # получить обновления
```

PDF остаются локально. В облако идут только метаданные, фрагменты и черновики.

---

## Документация

- [User Guide](docs/USER_GUIDE.md) — полное руководство
- [ADR](docs/adr/) — архитектурные решения
- [litresearch.ru](https://litresearch.ru) — облачный SaaS

---

## Лицензия

Ядро (`src/klemma/`) — [Polyform Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/). Бесплатно для академического и личного использования.

Для коммерческого лицензирования: ilya.bolkhovsky@gmail.com
