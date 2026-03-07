Analyze a scientific paper and create a structured annotation for a {{ project_type }}. Respond entirely in {{ language }}.

## Paper Metadata
- **Title**: {{ title }}
- **Authors**: {{ authors }}
- **Year**: {{ year }}
- **Journal**: {{ journal }}
- **DOI**: {{ doi }}
- **Language**: {{ paper_language }}

## Abstract (from metadata)
{{ abstract }}

## Full Text
{{ pdf_text }}

---

## Project Context
{{ dissertation_context }}

---

## Our Library (existing sources)
{{ library_entries }}

---

## Task

Create a JSON annotation with the following structure:

```json
{
  "summary": "3-5 sentences describing the paper's main contribution and results",
  "methodology": "Description of research methods, data sources, and analytical approaches",
  "key_findings": [
    "First key result",
    "Second key result",
    "Third key result"
  ],
  "relevance_to_dissertation": "Specific explanation of how the paper relates to the project topic",
  "quality_score": 4,
  "citation_priority": "medium",
  "dissertation_relevance": {
    "primary_chapter": 2,
    "primary_section": "2.3.1",
    "relevance_nr1": 3,
    "relevance_nr2": 4,
    "rationale": "Why the paper is relevant for scientific results NR1/NR2"
  },
  "chapters": [1, 2],
  "sections": ["1.3.1", "2.3.1"],
  "suggested_tags": ["Tag1", "Tag2"],
  "key_references": [
    {
      "authors": "Smith et al.",
      "year": 2020,
      "title": "Exact title from References section",
      "why_relevant": "Brief explanation of relevance to the project",
      "citation_intent": "method",
      "dissertation_sections": ["2.3.1"],
      "in_library": true,
      "citekey": "Smith2020"
    }
  ]
}
```

## Instructions

1. **summary**: Brief paper overview — what was done, what data, what result
2. **methodology**: What methods, data, time period
3. **key_findings**: 2-5 key results
4. **relevance_to_dissertation**: How specifically the paper connects to the project topic
5. **quality_score**: Rating 1-5 by relevance to the project:
   - 5 = Directly about the project's core topic and methods
   - 4 = About closely related methods or data applicable to the project
   - 3 = About the broader domain or general methods
   - 2 = Indirect connection (general methodology, related field)
   - 1 = Minimal relevance, background source
6. **citation_priority**: "high" (key source), "medium" (supporting), "low" (background)
7. **dissertation_relevance**:
   - primary_chapter: Main chapter (1-4)
   - primary_section: Specific section (e.g. "2.3.1")
   - relevance_nr1: Relevance for scientific result NR1, scale 0-5
   - relevance_nr2: Relevance for scientific result NR2, scale 0-5
   - rationale: Brief justification
8. **chapters**: All relevant chapters [1-4]
9. **sections**: All relevant sections
10. **suggested_tags**: 1-5 tags from the list: {{ available_tags }}
11. **key_references**: 5-15 most relevant references from the paper's bibliography:
   - Find the References/Bibliography section in the full text
   - Select 5-15 references most important for the project topic
   - For each: authors (short form), year, title (exact from References), why_relevant, citation_intent, dissertation_sections
   - **citation_intent**: how the paper being analyzed cites this reference (Teufel et al. 2006):
     - `background` — cited for context, literature review, general knowledge
     - `method` — cited as a method, algorithm, or approach that is used or adapted
     - `result_comparison` — cited for comparing results, benchmarks, or metrics
     - `extends` — cited as a foundation that the paper extends or improves upon
     - `contrasts` — cited to highlight disagreement or limitations
     - `uses_data` — cited for datasets, benchmarks, or empirical data used
   - **in_library**: true if the reference matches a source in "Our Library" above (match by author + year + title). Include citekey
   - **in_library**: false if the reference is not in our library. citekey = null
   - Priority: prefer references NOT in our library — these are gaps

Respond with ONLY valid JSON in {{ language }}. No markdown formatting, no explanations.
