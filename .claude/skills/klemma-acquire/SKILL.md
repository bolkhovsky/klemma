---
description: Add papers to klemma library — download PDF, create Zotero item, register in DB. Use when agent finds relevant papers and needs to add them.
allowed-tools: Bash(klemma acquire:*)
---

# klemma acquire — добавление статей в библиотеку

Автоматический pipeline: скачать PDF → добавить в Zotero → дождаться BBT citekey → зарегистрировать в klemma.

## Предусловия

Перед запуском acquire убедись:

1. `klemma info` — показывает секцию Zotero (library_id, storage_path)
2. `zotero.library_id` задан в конфиге (системный `~/.klemma/config.yaml` или проектный)
3. Zotero запущен (для BBT polling citekey)
4. Опционально: `ZOTERO_API_KEY` (нужен для cloud Zotero; для local — не обязателен)

Если `library_id` не задан — не переходи к batch. Сначала исправь конфиг.

## Перед batch

Проверь pipeline на одной статье:

```bash
klemma acquire <одна_ссылка> --title "Test Paper" --no-process
```

Если статус = ok — запускай batch. Если ошибка — исправь конфиг.

## Одна статья

```bash
klemma acquire <pdf_url> \
  --title "Заголовок статьи" \
  --authors "Фамилия И.О., Фамилия И.О." \
  --year 2022 \
  --journal "Название журнала" \
  --volume 68 --issue 3 \
  --section 1.2
```

Все параметры кроме URL опциональны, но рекомендуется указывать title, authors, year, section.

## Batch (рекомендуется при >3 статей)

1. Сформируй JSON-файл `/tmp/papers.json`:

```json
[
  {
    "url": "https://example.com/paper1.pdf",
    "title": "Название статьи",
    "authors": "Иванов А.Б., Петров В.Г.",
    "year": 2022,
    "journal": "Название журнала",
    "volume": "68",
    "issue": "3",
    "pages": "15-28",
    "doi": "10.1234/example",
    "sections": ["1.2", "1.3.1"]
  },
  {
    "url": "https://example.com/paper2.pdf",
    "title": "Другая статья",
    "authors": "Сидоров К.Л.",
    "year": 2023,
    "sections": ["2.1"]
  }
]
```

2. Вызови:

```bash
klemma acquire --batch /tmp/papers.json
```

## Флаги

- `--no-process` — только скачать и добавить в Zotero, не запускать извлечение фрагментов
- `--section` / `-s` — привязать к разделу диссертации (только для single mode; в batch используй `sections` в JSON)

## Формат authors

Формат: `"Фамилия И.О., Фамилия И.О."` — через запятую, инициалы после фамилии.
Примеры:
- `"Егорова Е.С., Миронов Е.У."`
- `"Smith J.A., Brown R."`

## Что происходит внутри

1. Скачивает PDF (проверяет размер > 10KB)
2. Создаёт запись в Zotero через API
3. Прикрепляет PDF как attachment
4. Ожидает появления BBT citekey (до 30 сек)
5. Регистрирует в klemma DB с привязкой к разделам
6. Запускает `klemma process <citekey>` (если нет `--no-process`)

## Типичный агентский workflow

1. Найти релевантные статьи (веб-поиск, архивы журналов)
2. Собрать метаданные и прямые ссылки на PDF
3. Сформировать `/tmp/papers.json`
4. `klemma acquire --batch /tmp/papers.json`
5. Проверить результат: `klemma status -ch N`
