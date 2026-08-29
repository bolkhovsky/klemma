"""Citation-graph gap discovery — rerank a seed's unowned neighbours.

Pure skill (ADR red lines: no ``state.py`` import, no file I/O, ``logger`` only).
Takes the citation-graph candidates the CLI fetched, drops the ones already in
the library (by DOI or normalized title), and ranks the rest by embedding
cosine-similarity to the seed paper. Title-only embedding by default (fast);
``deep=True`` folds in abstracts for sharper ranking.

The embedding provider is injected by the caller (like ``suggester`` receives a
search provider), and the seed vector must come from the *same* model as that
provider — the CLI enforces this before calling in.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GapCandidate:
    openalex_id: str
    doi: str
    title: str
    year: Optional[int]
    venue: str
    cited_by: int
    first_author: str
    relation: str  # "cites" | "ref" | "both"
    similarity: float = 0.0


@dataclass
class GapResult:
    gaps: list[GapCandidate]
    n_neighbours: int
    n_owned_suppressed: int


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def _norm_doi(d: str) -> str:
    return (d or "").replace("https://doi.org/", "").strip().lower()


def _cosine(a: list, b: list) -> float:
    """Plain cosine similarity (inlined to keep skills free of an embeddings import)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _embed_texts(embeddings, texts: list) -> list:
    """Embed a batch of strings, using native batching when the provider has it."""
    if hasattr(embeddings, "embed_batch"):
        return embeddings.embed_batch(texts)
    return [embeddings.embed(t) for t in texts]


def find_citation_gaps(
    seed_citekey: str,
    *,
    candidates: list,
    owned_dois: set,
    owned_titles: set,
    seed_vector: list,
    embeddings,
    deep: bool = False,
    limit: int = 15,
) -> GapResult:
    """Rerank unowned citation-graph neighbours by cosine similarity to the seed.

    - ``candidates``: ``Candidate`` objects from ``fetch_citation_graph`` (duck-typed:
      ``.doi``, ``.title``, ``.abstract``, ``.year``, ``.venue``, ``.cited_by``,
      ``.first_author``, ``.relation``, ``.openalex_id``).
    - ``owned_dois`` / ``owned_titles``: raw DOI/title values from the library
      (e.g. ``state.get_all_sources_metadata()``); normalized here so the CLI and
      skill share a single normalization rule.
    - ``seed_vector``: the seed's embedding, from the same model as ``embeddings``.
    """
    n_neighbours = len(candidates)

    # Normalize ownership sets here (single source of truth for the diff).
    owned_doi_set = {_norm_doi(d) for d in owned_dois if d}
    owned_title_set = {_norm_title(t) for t in owned_titles if t}

    # Ownership diff — by DOI first, then by normalized title.
    fresh = []
    for c in candidates:
        doi = _norm_doi(c.doi)
        if doi and doi in owned_doi_set:
            continue
        if _norm_title(c.title) in owned_title_set:
            continue
        fresh.append(c)
    n_owned = n_neighbours - len(fresh)

    if not fresh:
        logger.info(
            "gaps for %s: %d neighbours, all %d already owned",
            seed_citekey, n_neighbours, n_owned,
        )
        return GapResult(gaps=[], n_neighbours=n_neighbours, n_owned_suppressed=n_owned)

    # Embed candidate texts: title-only by default, title+abstract when deep.
    texts = [
        (f"{c.title}. {c.abstract}".strip() if deep else (c.title or "").strip())
        for c in fresh
    ]
    vecs = _embed_texts(embeddings, texts)

    ranked = []
    for c, vec in zip(fresh, vecs):
        ranked.append(
            GapCandidate(
                openalex_id=c.openalex_id,
                doi=c.doi,
                title=c.title,
                year=c.year,
                venue=c.venue,
                cited_by=c.cited_by,
                first_author=c.first_author,
                relation=c.relation,
                similarity=_cosine(seed_vector, vec) if vec else 0.0,
            )
        )
    ranked.sort(key=lambda g: g.similarity, reverse=True)
    logger.info(
        "gaps for %s: %d neighbours, %d owned-suppressed, %d ranked",
        seed_citekey, n_neighbours, n_owned, len(ranked),
    )
    return GapResult(
        gaps=ranked[:limit],
        n_neighbours=n_neighbours,
        n_owned_suppressed=n_owned,
    )
