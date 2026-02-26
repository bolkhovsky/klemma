"""Benchmark candidate discovery from citation graph.

Ranks processed sources by how many of their references are already
in the library — candidates with high coverage are best suited for
citation reconstruction benchmarks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from klemma.state import StateManager


class CandidateScore(BaseModel):
    citekey: str
    title: str = ""
    in_library_citations: int = 0
    total_citations: int = 0
    intent_diversity: int = 0  # 0–3 distinct intents in its refs
    has_pdf: bool = False
    already_benchmarked: bool = False
    score: float = 0.0


def discover_candidates(
    state: StateManager,
    limit: int = 10,
    benchmarked_citekeys: set[str] | None = None,
) -> list[CandidateScore]:
    """Find best benchmark candidates from citation graph.

    Ranks completed sources by:
    - Number of in-library citations (×3)
    - Intent diversity (×2)
    - Has PDF (+1)
    - Already benchmarked (−5)

    Args:
        state: StateManager instance.
        limit: Max candidates to return.
        benchmarked_citekeys: Pre-fetched set of already-benchmarked citekeys.
            If None, queries benchmark_runs table.
    """
    if benchmarked_citekeys is None:
        benchmarked_citekeys = state.get_benchmarked_citekeys()

    with state._conn() as conn:
        cur = conn.execute("""
            SELECT s.id, s.pdf_path,
                   COUNT(DISTINCT CASE WHEN cl.in_library=1
                         THEN cl.target_title_hash END) as in_lib,
                   COUNT(DISTINCT cl.target_title_hash) as total,
                   COUNT(DISTINCT cl.citation_intent) as intent_div
            FROM sources s
            JOIN citation_links cl ON cl.source_id = s.id
            WHERE s.status = 'completed'
            GROUP BY s.id
            HAVING in_lib >= 3
            ORDER BY in_lib DESC
        """)
        rows = cur.fetchall()

    candidates = []
    for row in rows:
        citekey = row["id"]
        has_pdf = bool(row["pdf_path"])
        already = citekey in benchmarked_citekeys
        in_lib = row["in_lib"]
        intent_div = row["intent_div"]
        score = in_lib * 3 + intent_div * 2 + int(has_pdf) - 5 * int(already)

        candidates.append(CandidateScore(
            citekey=citekey,
            in_library_citations=in_lib,
            total_citations=row["total"],
            intent_diversity=intent_div,
            has_pdf=has_pdf,
            already_benchmarked=already,
            score=score,
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def format_candidate_hint(candidates: list[CandidateScore], limit: int = 3) -> str:
    """One-line Rich markup for status bar display."""
    if not candidates:
        return ""
    items = []
    for c in candidates[:limit]:
        items.append(f"{c.citekey} ({c.in_library_citations} refs)")
    return "[dim]Benchmark candidates: " + ", ".join(items) + "[/dim]"
