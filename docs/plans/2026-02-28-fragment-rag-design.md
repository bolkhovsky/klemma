# Fragment RAG for `klemma ask` — Design

**Epic:** #28 (Epic-B Fragment RAG integration with A/B evaluation)

**Goal:** Add fragment-level semantic retrieval to `klemma ask` so the agent receives the most relevant citation fragments (not just source metadata) as context, producing more precise and grounded answers.

**Approach:** Minimal RAG (Approach A) — embed fragments, cosine retrieval, inject top-K into agent prompt.

---

## Architecture

Current `klemma ask` flow:
```
query → build_agent_context() → flat source list (citekey, quality, fragment_count) → LLM
```

New flow:
```
query → embed(query) → cosine_similarity(query_vec, fragment_vecs) → top-K fragments
     → build_agent_context(relevant_fragments=top_K) → agent.md with fragment text → LLM
```

Fallback: if no fragment embeddings exist, current behavior (source list only, no fragment text).

## Components

### 1. DB Migration v5

Add two columns to `fragments` table:
- `embedding BLOB` — float32 vector, same format as `sources.embedding`
- `embedding_model TEXT` — model name for versioning

### 2. Fragment Embedding Storage (FragmentRepository)

New methods in `repositories/fragments.py`:
- `save_fragment_embedding(fragment_id, embedding, model)` — store BLOB
- `get_fragment_embeddings(model?)` — return `{fragment_id: vector}` for all embedded fragments
- `get_fragment_embedding_stats()` — coverage stats (total, embedded, by model)

Reuses same `struct.pack/unpack` pattern as `EmbeddingsStoreRepository`.

### 3. Fragment Embedding CLI (`klemma embed --fragments`)

Extend existing `klemma embed` command:
- `--fragments` flag: embed all un-embedded fragments using configured `EmbeddingProvider`
- Pass `fragment_text` as `title` param to `EmbeddingProvider.embed()`
- Progress bar, skip already-embedded, report stats

### 4. Fragment Retrieval (FragmentRepository)

New method: `retrieve_similar_fragments(query_embedding, top_k, model?)`:
- Load all fragment embeddings for matching model
- Compute cosine similarity against query embedding
- Return top-K fragment dicts with similarity score
- Pure in-memory cosine (same pattern as gap semantic reranking)

### 5. Agent Context Enhancement (agent.py)

Modify `build_agent_context()`:
- Accept optional `embeddings: EmbeddingProvider` and `query: str` params
- If embeddings available: embed query → retrieve top-K fragments → pass to template
- Template gets new `relevant_fragments` variable

### 6. Prompt Template Update (agent.md)

Add `## Relevant Fragments` section (between Sources and Today's Plan):
```jinja2
{% if relevant_fragments %}
## Relevant Fragments ({{ relevant_fragments | length }})

{% for f in relevant_fragments %}
- @{{ f.citekey }} [{{ f.citation_intent or "?" }}] (sim={{ f.similarity }}): {{ f.fragment_text[:300] }}
{% endfor %}
{% endif %}
```

### 7. CLI Wiring (`klemma ask`)

- Initialize embeddings provider (already available in `kctx`)
- Pass `embeddings` and `query` to `build_agent_context()`
- Log context size delta (baseline vs RAG) for A/B comparison

### 8. A/B Evaluation

Compare baseline (no fragments) vs RAG (top-K fragments):
- Context token count: before/after
- Manual quality review on 5-10 representative queries
- Save results to `klemma-paper/results/fragment_rag_evaluation.md`

## Design Decisions

- **Fragment text only for embedding input** — no metadata prefix, no source context concatenation. Keeps semantic signal clean.
- **In-memory cosine** — with ~2500 fragments at 768-dim, the full matrix is ~7.5MB. Fast enough for interactive use.
- **top_k=10 default** — balances context quality vs token budget. Each fragment ~50-150 tokens, so ~500-1500 tokens added.
- **No section filtering** — free-form queries don't reliably map to sections. Let cosine similarity handle relevance.
- **Backward compatible** — no fragments embedded = no RAG section in prompt = identical to current behavior.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Fragment text too short for meaningful embeddings | SPECTER/OpenAI both handle short text; test with real data |
| Context window budget exceeded | top_k=10 cap, truncate fragments to 300 chars in prompt |
| Embedding model mismatch (query vs stored) | Filter by model name, warn if mismatch |

## Testing

- Unit: fragment embedding save/retrieve roundtrip
- Unit: top-K retrieval ranking (known vectors → known order)
- Unit: agent context building with/without fragments (size comparison)
- Unit: `klemma embed --fragments` with mocked provider
- Integration: manual `klemma ask` comparison
