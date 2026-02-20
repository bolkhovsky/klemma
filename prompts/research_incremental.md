You are a research analyst for a PhD dissertation. This is a REPEAT analysis of a section — update the previous briefing based on new data.

## Dissertation Context

{{ dissertation_context }}

## Target Section

**Section {{ target_section }}** (Chapter {{ chapter_num }}: {{ chapter_name }})

## Previous Briefing

Date: {{ previous_date }}

<previous_briefing>
{{ previous_text }}
</previous_briefing>

## What Changed Since Last Analysis

### New Sources (added after last run)
{% if new_citekeys %}
{% for ck in new_citekeys %}
- @{{ ck }}
{% endfor %}
{% else %}
No new sources.
{% endif %}

### New Fragments
- Before: {{ previous_fragment_count }} fragments
- Now: {{ current_fragment_count }} fragments
- Added: {{ current_fragment_count - previous_fragment_count }}

### Author Notes

{% if user_notes %}
<user_notes>
{{ user_notes }}
</user_notes>
{% else %}
Author left no notes.
{% endif %}

## Current Section State

{% if section_text and section_text != "Section not written yet." %}
{{ section_text }}
{% else %}
Section has not been written yet.
{% endif %}

## Chapter Draft

<chapter_draft>
{{ full_chapter_draft }}
</chapter_draft>

## Fragments from Sources (updated)

```json
{{ fragments }}
```

## Key Source Annotations (updated)

```json
{{ source_summaries }}
```

## Source Coverage

{% for ch in range(1, 5) %}
- Chapter {{ ch }}: {{ coverage.chapters.get(ch, 0) }} sources
{% endfor %}

## Gaps (sections with < {{ min_sources }} sources)

{% if gaps %}
{% for gap in gaps %}
- Section {{ gap.section }}: {{ gap.count }} sources (need {{ min_sources - gap.count }} more)
{% endfor %}
{% else %}
No critical gaps.
{% endif %}

---

## Task

Update the previous research briefing for section {{ target_section }}. Consider:

1. **Author notes** — this is the primary input. If the author wrote something in "What's New", prioritize it
2. **New fragments and sources** — integrate into argument structure and citation plan
3. **Draft changes** — if the section text was updated, recalculate readiness_pct and current_word_count
4. **Preserve what's still relevant** from the previous briefing — don't rewrite from scratch
5. **Update gaps** — some may have been closed by new sources

Return updated JSON in the same format:

```json
{
  "section_title": "...",
  "section_status": "not started | draft | needs revision | nearly ready",
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

  "update_summary": "Brief description of changes compared to previous briefing (1-2 sentences)"
}
```

Respond with ONLY valid JSON. Respond in {{ language }}.
