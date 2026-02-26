"""Prepare benchmark: fetch missing referenced papers.

For a given paper (citekey), queries its citation_links for references
not yet in the library, resolves their PDFs via arXiv/CrossRef/Unpaywall,
and acquires them using the existing acquirer pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from .resolvers import ResolvedPaper, resolve_pdf_url

if TYPE_CHECKING:
    from klemma.state import StateManager

logger = logging.getLogger(__name__)


class ReferenceStatus(BaseModel):
    title: str
    target_citekey: Optional[str] = None
    status: str = ""  # "in_library" | "fetched" | "no_pdf" | "failed"
    resolved: Optional[ResolvedPaper] = None


class PrepareResult(BaseModel):
    paper_citekey: str
    total_references: int = 0
    in_library: int = 0
    fetched: int = 0
    unfetchable: int = 0
    references: list[ReferenceStatus] = []


def prepare_benchmark(
    state: StateManager,
    citekey: str,
    storage_path: str,
    dry_run: bool = False,
) -> PrepareResult:
    """Resolve and optionally fetch missing referenced papers.

    1. Query citation_links for the paper
    2. Identify refs not in library (in_library=0)
    3. For each: resolve_pdf_url()
    4. If not dry_run: acquire via acquire_paper_local()
    """
    links = state.get_citation_links(source_id=citekey)
    if not links:
        return PrepareResult(paper_citekey=citekey)

    result = PrepareResult(
        paper_citekey=citekey,
        total_references=len(links),
    )

    for link in links:
        title = link.get("target_title", "")
        target_ck = link.get("target_citekey", "")
        in_lib = link.get("in_library", False)

        if in_lib:
            result.in_library += 1
            result.references.append(ReferenceStatus(
                title=title,
                target_citekey=target_ck,
                status="in_library",
            ))
            continue

        # Resolve PDF URL
        authors = link.get("target_authors", "")
        year = link.get("target_year")
        resolved = resolve_pdf_url(title, authors, year)

        if not resolved.pdf_url:
            result.unfetchable += 1
            result.references.append(ReferenceStatus(
                title=title,
                target_citekey=target_ck,
                status="no_pdf",
                resolved=resolved,
            ))
            continue

        if dry_run:
            result.references.append(ReferenceStatus(
                title=title,
                target_citekey=target_ck,
                status="resolved",
                resolved=resolved,
            ))
            continue

        # Fetch via acquirer
        try:
            from klemma.skills.acquirer import PaperMetadata, acquire_paper_local
            meta = PaperMetadata(
                url=resolved.pdf_url,
                title=resolved.title or title,
                authors=resolved.authors or authors,
                year=resolved.year or year,
                doi=resolved.doi,
            )
            acq_result = acquire_paper_local(meta, storage_path=storage_path, state=state)
            if acq_result.status == "ok":
                result.fetched += 1
                result.references.append(ReferenceStatus(
                    title=title,
                    target_citekey=acq_result.citekey,
                    status="fetched",
                    resolved=resolved,
                ))
            else:
                result.unfetchable += 1
                result.references.append(ReferenceStatus(
                    title=title,
                    status="failed",
                    resolved=resolved,
                ))
        except Exception as e:
            logger.error("Failed to acquire %s: %s", title[:50], e)
            result.unfetchable += 1
            result.references.append(ReferenceStatus(
                title=title,
                status="failed",
                resolved=resolved,
            ))

    return result
