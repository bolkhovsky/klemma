Ты — исследовательский аналитик для кандидатской диссертации. Это ПОВТОРНЫЙ анализ раздела — обнови предыдущий брифинг на основе новых данных.

## Контекст диссертации

{{ dissertation_context }}

## Целевой раздел

**Раздел {{ target_section }}** (Глава {{ chapter_num }}: {{ chapter_name }})

## Предыдущий брифинг

Дата: {{ previous_date }}

<previous_briefing>
{{ previous_text }}
</previous_briefing>

## Что изменилось с прошлого анализа

### Новые источники (добавлены после прошлого запуска)
{% if new_citekeys %}
{% for ck in new_citekeys %}
- @{{ ck }}
{% endfor %}
{% else %}
Новых источников нет.
{% endif %}

### Новые фрагменты
- Было: {{ previous_fragment_count }} фрагментов
- Стало: {{ current_fragment_count }} фрагментов
- Добавлено: {{ current_fragment_count - previous_fragment_count }}

### Заметки автора

{% if user_notes %}
<user_notes>
{{ user_notes }}
</user_notes>
{% else %}
Автор не оставил заметок.
{% endif %}

## Текущее состояние раздела

{% if section_text and section_text != "Раздел ещё не написан." %}
{{ section_text }}
{% else %}
Раздел ещё не написан.
{% endif %}

## Черновик главы

<chapter_draft>
{{ full_chapter_draft }}
</chapter_draft>

## Фрагменты из источников (обновлённые)

```json
{{ fragments }}
```

## Аннотации ключевых источников (обновлённые)

```json
{{ source_summaries }}
```

## Покрытие источниками

{% for ch in range(1, 5) %}
- Глава {{ ch }}: {{ coverage.chapters.get(ch, 0) }} источников
{% endfor %}

## Пробелы (разделы с < {{ min_sources }} источников)

{% if gaps %}
{% for gap in gaps %}
- Раздел {{ gap.section }}: {{ gap.count }} источников (нужно ещё {{ min_sources - gap.count }})
{% endfor %}
{% else %}
Критических пробелов нет.
{% endif %}

---

## Задача

Обнови предыдущий исследовательский брифинг для раздела {{ target_section }}. Учти:

1. **Заметки автора** — это главный вход. Если автор написал что-то в «Что нового», учти это в первую очередь
2. **Новые фрагменты и источники** — интегрируй в структуру аргументации и план цитирования
3. **Изменения в черновике** — если текст раздела обновился, пересчитай readiness_pct и current_word_count
4. **Сохрани то, что осталось актуальным** из предыдущего брифинга — не переписывай с нуля
5. **Обнови пробелы** — возможно, часть из них закрыта новыми источниками

Верни обновлённый JSON в том же формате:

```json
{
  "section_title": "...",
  "section_status": "не начат | черновик | требует доработки | почти готов",
  "current_word_count": 0,
  "target_word_count": 1000,
  "readiness_pct": 0,

  "fragment_distribution": {
    "quote": 0, "methodology": 0, "result": 0,
    "key_idea": 0, "definition": 0, "conclusion": 0
  },

  "argument_blocks": [
    {
      "order": 1,
      "title": "...",
      "description": "...",
      "citations": ["citekey1"],
      "estimated_words": 300
    }
  ],

  "citation_plan": [
    {
      "citekey": "citekey1",
      "fragment_text": "...",
      "usage": "evidence | method | comparison | definition | quote",
      "position": "...",
      "relevance": 5
    }
  ],

  "missing_coverage": ["..."],

  "writing_suggestions": ["..."],

  "update_summary": "Краткое описание изменений по сравнению с предыдущим брифингом (1-2 предложения)"
}
```

Верни ТОЛЬКО валидный JSON, на русском языке.
