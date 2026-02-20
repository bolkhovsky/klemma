---
description: Check dissertation coverage, gaps, and source statistics. Use to verify results after acquire/process or to understand current state.
allowed-tools: Bash(klemma status:*)
---

# klemma status — статус диссертации

Показывает покрытие по главам, пробелы, статистику источников и фрагментов.

## Использование

```bash
# Общий статус
klemma status

# По конкретной главе
klemma status -ch 1

# Подробный (с разбивкой по разделам)
klemma status --verbose

# Глава + подробно
klemma status -ch 2 --verbose
```

## Что показывает

- Источники по главам (total / processed / pending)
- Фрагменты по типам (цитата, аргумент, метод, данные)
- Пробелы — разделы с недостаточным покрытием
- Reference gaps — недостающие ссылки из библиографий
- Prune verdicts — рекомендации по очистке библиотеки

## Когда вызывать

- После `klemma acquire` + `klemma process` — проверить что статьи добавлены
- Для оценки покрытия перед поиском новых статей
- При ответе на вопросы о состоянии диссертации
