## Project

**Name:** {{ project_name }}

**Outline:**

{{ outline_md }}

## Loaded sources ({{ rationale_language }} language context)

The researcher has already added these papers to the library. Use their topics
to anchor relevance judgements for the candidates below.

{{ loaded_sources_md }}

## Candidates

These are the papers cited by the loaded sources but not yet in the library.
Each line shows: rank, title, authors, year, cited_by_count, and inferred
citation intent. Select the top {{ max_recommendations }} that the researcher
should read next, using the criteria and integrity rules from the system
message.

{{ candidates_md }}

Return the JSON block only.
