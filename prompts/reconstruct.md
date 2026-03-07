You are an AI citation recommendation system. You are helping an author write a scientific paper. Given the paper's outline, thesis, and a library of sources with extracted fragments, recommend which sources should be cited in each section.

## Paper Context

**Title**: {{ paper_title }}

{% if abstract %}
**Abstract**: {{ abstract }}
{% endif %}

{% if keywords %}
**Keywords**: {{ keywords | join(", ") }}
{% endif %}

## Paper Outline

{% for section in sections %}
### {{ section.section_id }}: {{ section.title }}
{% if section.description %}
{{ section.description }}
{% endif %}
{% endfor %}

## Available Sources

{% for source in sources %}
#### {{ source.citekey }} — {{ source.title }} ({{ source.year }})
{% if source.abstract %}
> {{ source.abstract[:300] }}
{% endif %}
{% if source.fragments %}
Key fragments:
{% for frag in source.fragments[:5] %}
- [{{ frag.intent }}] {{ frag.text[:200] }}
{% endfor %}
{% endif %}
{% endfor %}

---

## Instructions

You are assisting an author who is writing the paper described above. For each section in the outline, recommend sources from the library that should be cited there. Think about:

- **What argument does each section make?** Match sources that support or contrast with that argument.
- **What methodology is described?** Match sources that provide the theoretical basis or comparable methods.
- **What results are discussed?** Match sources whose results can be compared.

For each recommendation, specify:

1. **section_id** — which section (must match an outline section ID)
2. **citekey** — which source (must be from the provided library)
3. **intent** — how the source would be cited (Teufel et al. 2006 citation function taxonomy):
   - `background` — provides context, general knowledge, or prior work
   - `method` — provides a method, algorithm, or technique to build upon
   - `result_comparison` — provides results or metrics for comparison
   - `extends` — the paper extends, builds upon, or improves the cited approach
   - `contrasts` — the paper disagrees with or shows limitations of the cited work
   - `uses_data` — uses datasets, benchmarks, or empirical data from the cited work
4. **justification** — brief reason for the recommendation (1 sentence)

Rules:
- Only recommend sources from the provided library (no external sources)
- A source can appear in multiple sections with different intents
- Prefer specific fragment evidence over general relevance
- Use section descriptions to understand what each section needs
{% if max_recs_per_section %}
- Recommend at most {{ max_recs_per_section }} sources per section — only the most relevant
{% else %}
- Be thorough: if a source is relevant to a section, include it
{% endif %}
- Order recommendations by confidence (most confident first within each section)
{% if examples %}

## Examples

Here are examples of correct citation assignments for similar papers:

{% for ex in examples %}
**Section {{ ex.section_id }}: {{ ex.section_title }}**
→ {{ ex.citekey }} ({{ ex.intent }}): {{ ex.justification }}
{% endfor %}
{% endif %}

## Output Format

Return ONLY valid JSON:

```json
{
  "recommendations": [
    {
      "section_id": "2.1",
      "citekey": "smith2020",
      "intent": "method",
      "justification": "Smith's algorithm is the basis for our approach"
    }
  ]
}
```

Respond with ONLY valid JSON.
