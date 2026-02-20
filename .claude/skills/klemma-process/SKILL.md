---
description: Extract citation fragments from PDFs. Use after acquiring new papers or when sources have status 'pending'.
allowed-tools: Bash(klemma process:*)
---

# klemma process — извлечение фрагментов из PDF

Анализирует PDF через Claude AI, извлекает цитаты, аргументы, методы и данные, сохраняет в SQLite и vault.

## Использование

```bash
# Одна статья
klemma process smithIceForecast2022

# Несколько статей (параллельно)
klemma process smith2022 jones2023 brown2024

# Несколько статей (последовательно)
klemma process smith2022 jones2023 --serial

# Все pending источники
klemma process
```

## Когда вызывать

- После `klemma acquire` — обработать новые статьи
- Когда `klemma status` показывает источники со статусом `pending`
- По запросу пользователя для конкретных citekey

## Результат

- Фрагменты сохраняются в SQLite (`fragments` таблица)
- Создаётся/обновляется vault-заметка `@citekey.md` с секцией цитат
- Статус источника меняется: `pending` → `processed`
