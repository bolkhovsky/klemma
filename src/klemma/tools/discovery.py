"""Discovery agent — hybrid pipeline for finding new literature.

Phase 1 (deterministic): MCP search for each ref-gap and section topic.
Phase 2 (Claude): relevance assessment of raw results.

Can be run as subprocess for background execution:
    python -m klemma.tools.discovery --section 1.3.2 --config config.yaml
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def run_discovery(
    section: str,
    config_path: str,
    max_results: int = 20,
    skip_assessment: bool = False,
) -> dict:
    """Run the full discovery pipeline for a section.

    Returns dict with keys: searched, found, assessed, errors.
    """
    from ..config import load_config
    from ..state import StateManager

    cfg = load_config(config_path)
    state = StateManager(cfg.state.db_path)

    if "academia" not in cfg.mcp.servers:
        logger.error("Academia MCP server not configured")
        return {"searched": 0, "found": 0, "assessed": 0, "errors": ["academia MCP not configured"]}

    from .registry import ToolRegistry

    registry = ToolRegistry(cfg)

    # Phase 1: deterministic search
    raw_results = _phase1_search(section, state, registry, max_results)

    if not raw_results:
        return {"searched": raw_results.get("queries", 0) if isinstance(raw_results, dict) else 0,
                "found": 0, "assessed": 0, "errors": []}

    # Deduplicate against existing library
    from ..library_provider import create_library

    library = create_library(cfg)
    existing_keys = set(library.entries.keys())
    new_results = [r for r in raw_results if r.get("external_id") not in existing_keys]

    # Save raw discoveries
    for r in new_results:
        state.save_discovery(
            section=section,
            source_type=r.get("source", "arxiv"),
            external_id=r.get("external_id", ""),
            title=r.get("title", ""),
            authors=r.get("authors", ""),
            year=r.get("year"),
            abstract=r.get("abstract", ""),
            raw_data=json.dumps(r, ensure_ascii=False),
        )

    found = len(new_results)
    assessed = 0

    # Phase 2: Claude assessment (if not skipped and AI available)
    if not skip_assessment and found > 0:
        try:
            assessed = _phase2_assess(section, state, cfg)
        except Exception as e:
            logger.warning("Assessment phase failed: %s", e)

    return {
        "searched": len(raw_results),
        "found": found,
        "assessed": assessed,
        "errors": [],
    }


def _phase1_search(
    section: str,
    state,
    registry,
    max_results: int,
) -> list[dict]:
    """Phase 1: deterministic MCP search.

    Searches by:
    1. Open reference gaps for this section
    2. Section topic keywords
    """
    results = []

    # 1. Search for open reference gaps
    ref_gaps = state.get_reference_gaps(limit=10)
    section_gaps = [g for g in ref_gaps if section in (g.get("dissertation_sections") or "")]

    for gap in section_gaps[:5]:
        authors = gap.get("ref_authors", "")
        year = gap.get("ref_year", "")
        title = gap.get("ref_title", "")
        query = f"{authors} {year} {title}".strip()
        if not query:
            continue

        result = registry.call("academia", "arxiv_search", {"query": query, "limit": 3})
        if not result.is_error and result.content:
            parsed = _parse_search_results(result.content, "arxiv", gap_id=gap.get("id"))
            results.extend(parsed)

    # 2. General section topic search if few gap results
    if len(results) < max_results // 2:
        result = registry.call("academia", "arxiv_search", {"query": section, "limit": 5})
        if not result.is_error and result.content:
            parsed = _parse_search_results(result.content, "arxiv")
            results.extend(parsed)

    # Deduplicate by external_id
    seen = set()
    unique = []
    for r in results:
        eid = r.get("external_id", r.get("title", ""))
        if eid not in seen:
            seen.add(eid)
            unique.append(r)

    return unique[:max_results]


def _parse_search_results(content: str, source: str, gap_id: Optional[int] = None) -> list[dict]:
    """Parse MCP search results into normalized dicts."""
    results = []
    # Try JSON first
    try:
        items = json.loads(content)
        if isinstance(items, list):
            for item in items:
                results.append({
                    "source": source,
                    "external_id": item.get("id") or item.get("arxiv_id") or item.get("doi", ""),
                    "title": item.get("title", ""),
                    "authors": item.get("authors", ""),
                    "year": item.get("year"),
                    "abstract": item.get("abstract", ""),
                    "matched_gap_id": gap_id,
                })
            return results
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: treat as markdown text, extract basic info
    # Each result typically starts with "## N. Title"
    import re

    blocks = re.split(r"\n##\s+\d+\.", content)
    for block in blocks[1:]:  # skip preamble
        title_match = re.match(r"\s*(.+?)(?:\n|$)", block)
        title = title_match.group(1).strip() if title_match else ""
        authors_match = re.search(r"\*\*Authors?\*\*:?\s*(.+?)(?:\n|$)", block)
        authors = authors_match.group(1).strip() if authors_match else ""
        year_match = re.search(r"\*\*(?:Date|Year)\*\*:?\s*(\d{4})", block)
        year = int(year_match.group(1)) if year_match else None
        id_match = re.search(r"`([A-Z0-9]+)`|arxiv.org/abs/(\S+)", block)
        ext_id = (id_match.group(1) or id_match.group(2)) if id_match else ""

        if title:
            results.append({
                "source": source,
                "external_id": ext_id,
                "title": title,
                "authors": authors,
                "year": year,
                "abstract": "",
                "matched_gap_id": gap_id,
            })

    return results


def _phase2_assess(section: str, state, cfg) -> int:
    """Phase 2: Claude assesses relevance of pending discoveries."""
    from ..ai import create_ai

    ai = create_ai(cfg.ai)
    pending = state.get_discoveries(section=section, status="pending", limit=20)
    if not pending:
        return 0

    papers_text = "\n".join(
        f"- [{d['id']}] {d['title']} ({d['authors']}, {d.get('year', '?')}): {(d.get('abstract') or '')[:200]}"
        for d in pending
    )

    system = "You are a research librarian. Assess paper relevance for a dissertation section. Return JSON only."
    prompt = (
        f"Section: {section}\n\n"
        f"Papers to assess:\n{papers_text}\n\n"
        f"For each paper, return a JSON array with objects: "
        f'{{"id": <int>, "relevance": 1-5, "usage": "evidence|method|comparison|background", '
        f'"priority": "high|medium|low"}}'
    )

    data = ai.call_json(system, prompt, max_tokens=2048)
    if not data:
        return 0

    assessed = 0
    items = data if isinstance(data, list) else data.get("assessments", data.get("papers", []))
    for item in items:
        disc_id = item.get("id")
        if disc_id is None:
            continue
        state.review_discovery(disc_id, "assessed")
        # Update relevance in raw — we store it via a simple update
        try:
            from contextlib import contextmanager

            with state._conn() as conn:
                conn.execute(
                    "UPDATE discoveries SET relevance_score = ?, usage_type = ?, priority = ? WHERE id = ?",
                    (item.get("relevance"), item.get("usage", ""), item.get("priority", "medium"), disc_id),
                )
            assessed += 1
        except Exception:
            pass

    return assessed


def main():
    """CLI entry point for background execution."""
    import argparse

    parser = argparse.ArgumentParser(description="Klemma discovery agent")
    parser.add_argument("--section", "-s", required=True)
    parser.add_argument("--config", "-c", default="config.yaml")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--no-assess", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_discovery(
        section=args.section,
        config_path=args.config,
        max_results=args.max_results,
        skip_assessment=args.no_assess,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
