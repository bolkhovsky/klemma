"""Fragment extraction skill — extracts citation fragments from PDFs."""

import logging
from pathlib import Path
from typing import Optional

from ..ai import ClaudeClient
from ..config import KlemmaConfig
from ..literature.models import ExtractionResult, Fragment, ZoteroEntry
from ..literature.pdf import PDFExtractor
from ..state import StateManager
from .planner import DISSERTATION_CONTEXT

logger = logging.getLogger(__name__)

AVAILABLE_TAGS = [
    "Sea Ice", "Arctic", "Climate", "Forecasting", "Navigation", "Icebreaking",
    "GIS", "Machine Learning", "LSTM", "ConvLSTM", "U-Net", "CNN",
    "Transformer", "Classical ML", "Statistics", "Physical Model",
    "Validation", "Metrics",
    "Remote Sensing", "SAR", "Sentinel-1", "Microwave", "AMSR", "SSM-I",
    "Optical", "ERA5", "Ice Products", "AARI",
    "Barents Sea", "Kara Sea", "Laptev Sea", "Pacific Arctic", "Antarctic",
    "Review", "Dataset",
]


def extract_fragments(
    entry: ZoteroEntry,
    pdf_text: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: ClaudeClient,
) -> Optional[ExtractionResult]:
    """Extract citation fragments from a paper's PDF text."""

    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "extract.md"
    user_prompt = ai.render_prompt(
        prompt_path,
        title=entry.title or "Unknown",
        authors=entry.authors_str,
        year=entry.year or "Unknown",
        journal=entry.container_title or "N/A",
        doi=entry.DOI or "N/A",
        abstract=entry.abstract or "Not available",
        pdf_text=pdf_text,
        dissertation_context=DISSERTATION_CONTEXT,
        available_tags=", ".join(AVAILABLE_TAGS),
    )

    system = (
        "You are a research assistant extracting citation-worthy fragments from scientific papers. "
        "Output only valid JSON with fragments array."
    )

    data = ai.call_json(system, user_prompt, max_tokens=4096)
    if not data:
        logger.error("Failed to extract fragments for %s", entry.id)
        return None

    # Parse fragments
    fragments = []
    for f_data in data.get("fragments", []):
        try:
            fragment = Fragment(
                text=f_data.get("text", ""),
                type=f_data.get("type", "key_idea"),
                chapter=f_data.get("chapter"),
                section=f_data.get("section"),
                relevance=max(1, min(5, f_data.get("relevance", 3))),
                usage_hint=f_data.get("usage_hint", ""),
                page=f_data.get("page"),
            )
            if fragment.text:
                fragments.append(fragment)
        except Exception as e:
            logger.warning("Invalid fragment: %s", e)

    if not fragments:
        logger.warning("No valid fragments extracted for %s", entry.id)
        return None

    # Save to database
    fragment_dicts = [
        {
            "text": f.text,
            "type": f.type,
            "chapter": f.chapter,
            "section": f.section,
            "relevance": f.relevance,
            "usage_hint": f.usage_hint,
            "page": f.page,
        }
        for f in fragments
    ]
    saved = state.save_fragments(entry.id, fragment_dicts)
    logger.info("Saved %d fragments for %s", saved, entry.id)

    return ExtractionResult(
        source_id=entry.id,
        fragments=fragments,
        summary=data.get("summary", ""),
    )


def extract_from_citekey(
    citekey: str,
    config: KlemmaConfig,
    state: StateManager,
    ai: ClaudeClient,
    pdf_extractor: PDFExtractor,
    pdf_search_paths: list[Path],
) -> Optional[ExtractionResult]:
    """Full extraction pipeline: find source, get PDF, extract fragments."""

    # Check if source exists in state
    source = state.get_source(citekey)
    if not source:
        logger.error("Source %s not found in database", citekey)
        return None

    # Try to find PDF
    pdf_path = pdf_extractor.find_pdf(
        citekey,
        pdf_search_paths,
        entry_title="",
        direct_path=source.get("pdf_path"),
    )

    if not pdf_path:
        logger.error("PDF not found for %s", citekey)
        return None

    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < config.processing.min_pdf_length:
        logger.error("PDF text too short or extraction failed for %s", citekey)
        return None

    # Build a minimal ZoteroEntry from state data
    entry = ZoteroEntry(id=citekey, title=citekey)

    return extract_fragments(entry, pdf_text, config, state, ai)
