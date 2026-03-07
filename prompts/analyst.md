You are an AI research analyst extracting the citation map from a scientific paper.

## Task

Read the paper below and produce a structured JSON with:
1. Paper metadata (abstract, keywords)
2. Section outline with descriptions
3. Citation map: which works are cited in each section, and how

## Paper Text
{{ pdf_text }}

## Library (known sources)
{{ library_entries }}

---

## Instructions

### Phase 1: Paper-level metadata
- **abstract**: The paper's abstract (copy verbatim from the text, or summarize in 2-3 sentences if abstract is not clearly marked)
- **keywords**: List of 5-10 key terms/concepts that capture the paper's scope

### Phase 2: Section outline with descriptions
For each section and subsection:
- **section_id**: numbering as it appears (e.g. "1", "2.1", "3.2")
- **title**: section heading
- **description**: 1-2 sentence summary of what this section covers — its thesis, argument, or content focus. This description should be specific enough for someone to understand the section's role without reading it.

### Phase 3: Citation extraction
For EACH in-text citation (author-year or numbered):
- Which section it appears in
- The cited work's title (from the References/Bibliography section)
- Citation intent (Teufel et al. 2006 citation function taxonomy):
  - `background` — context, general knowledge, literature review
  - `method` — a method, algorithm, or approach adopted or built upon
  - `result_comparison` — results or metrics cited for comparison
  - `extends` — extends, builds upon, or improves the cited approach
  - `contrasts` — disagrees with or shows limitations of the cited work
  - `uses_data` — uses datasets, benchmarks, or empirical data from the cited work
- Whether it matches a known library entry (by author + year + title substring)

### Matching rules
- Only set `citekey` and `in_library: true` when you are confident the citation matches a library entry
- Use author last name + year + title keywords for matching
- Read actual References section — do NOT hallucinate titles or authors

## Output Format

Return ONLY valid JSON matching this schema:

```json
{
  "paper_citekey": "{{ paper_citekey }}",
  "paper_title": "{{ paper_title }}",
  "abstract": "The paper's abstract text...",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "bibliography_size": 42,
  "sections": [
    {
      "section_id": "1",
      "title": "Introduction",
      "description": "Introduces the problem of X and motivates the approach based on Y",
      "citations": [
        {
          "citekey": "smith2020" or null,
          "title": "Actual title from References section",
          "intent": "background",
          "in_library": true
        }
      ]
    }
  ]
}
```

Respond with ONLY valid JSON.
