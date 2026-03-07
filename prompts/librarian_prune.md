The library is oversaturated: {{ source_count }} sources (target: 100-120).
Analyze the list and suggest candidates for removal.

## DROP Criteria (confident removal)

- No fragments (f=0) AND no section assignment (s empty) — orphan sources
- quality <= 2 in non-priority sections
- Duplicate coverage: section already has 8+ sources, this one adds no unique value
- Status pending + low quality/relevance (not worth processing time)

## MAYBE Criteria (user's discretion)

- quality 3 with few fragments in an oversaturated section
- Outdated (>10 years) when a newer equivalent exists in the same section

## Protected (do NOT touch)

- Sources with high quality (4-5) in priority sections
- The only source or one of <=3 sources in a section

## Sources

{{ sources_compact }}

## Response Format (JSON)

```json
{
  "drop": [
    {"citekey": "authorTitleYear", "reason": "f=0, no section, quality=1"}
  ],
  "maybe": [
    {"citekey": "authorTitleYear", "reason": "quality=3, 1 fragment, section 1.3 has 12 sources"}
  ]
}
```

Respond entirely in {{ language }}. Respond with ONLY valid JSON. Include only real citekeys from the list above.
