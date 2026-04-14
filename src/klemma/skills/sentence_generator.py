"""Suggested sentences — academic formulations per fragment (ADR-017).

Pure skill: no DB I/O, no state.py import. Renders `prompts/suggest_sentence.md`,
calls AI, parses JSON, reports per-fragment success/failure.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import resolve_prompt

logger = logging.getLogger(__name__)


@dataclass
class SentenceResult:
    """Result of a suggested-sentence batch for a single source."""

    sentences: dict[str, str] = field(default_factory=dict)  # fragment_id → sentence
    failed: list[str] = field(default_factory=list)  # fragment_ids the model skipped / malformed
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


_AUTHOR_SPLIT = re.compile(r"\s*(?:;|,?\s+and\s+|,)\s*", re.IGNORECASE)


def _normalize_authors(authors: str) -> list[dict[str, str]]:
    """Parse a free-form authors string into `[{last, first_initial}, ...]`.

    Handles: ``"Smith, J."``, ``"J. Smith"``, ``"Smith, J.; Doe, A."``,
    ``"Jane Smith and John Doe"``. Returns up to 10 authors. Each entry has
    ``last`` (string, may be Latin or Cyrillic) and ``first_initial``
    (single char + dot, or empty string). The prompt transliterates
    Latin → Cyrillic itself — we only split the raw string.
    """
    if not authors:
        return []
    result: list[dict[str, str]] = []
    # Handle "Smith, J.; Doe, A." or "Smith, J., Doe, A."
    # Detect "Lastname, Initial" pattern — split on ";" first, fallback to comma-pairing
    chunks: list[str]
    if ";" in authors:
        chunks = [c.strip() for c in authors.split(";") if c.strip()]
    else:
        chunks = _split_authors_smart(authors)
    for chunk in chunks[:10]:
        entry = _parse_one_author(chunk)
        if entry:
            result.append(entry)
    return result


def _split_authors_smart(authors: str) -> list[str]:
    """Split a comma-free or 'Author and Author' style author list."""
    # Replace " and " with ";", then try pair-of-commas split "Last, F., Last, F."
    normalized = re.sub(r"\s+and\s+", ";", authors, flags=re.IGNORECASE)
    if ";" in normalized:
        return [c.strip() for c in normalized.split(";") if c.strip()]
    # If commas pair up as "Last, F." entries, split every 2 commas
    parts = [p.strip() for p in normalized.split(",") if p.strip()]
    # Heuristic: if parts alternate "Last" / "F." pattern, pair them
    if len(parts) >= 2 and all(
        re.fullmatch(r"[A-Za-z\.\s\-]+", p) for p in parts
    ):
        paired: list[str] = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and re.fullmatch(r"[A-Z]\.?(\s*[A-Z]\.?)?", parts[i + 1].strip()):
                paired.append(f"{parts[i]}, {parts[i + 1]}")
                i += 2
            else:
                paired.append(parts[i])
                i += 1
        return paired
    return parts


def _parse_one_author(chunk: str) -> Optional[dict[str, str]]:
    """Parse a single author chunk into ``{last, first_initial}``."""
    chunk = chunk.strip()
    if not chunk:
        return None
    if "," in chunk:
        last, _, rest = chunk.partition(",")
        last = last.strip()
        rest = rest.strip()
    else:
        tokens = chunk.split()
        if len(tokens) == 1:
            last = tokens[0]
            rest = ""
        else:
            # "J. Smith" or "John Smith" — last token is surname
            last = tokens[-1]
            rest = " ".join(tokens[:-1])
    initial = ""
    if rest:
        first_char = re.search(r"[A-Za-zА-Яа-яЁё]", rest)
        if first_char:
            initial = f"{first_char.group(0).upper()}."
    if not last:
        return None
    return {"last": last, "first_initial": initial}


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first top-level JSON object from raw model output.

    Handles prose wrapping and markdown fences. Returns None if no parseable
    object is found.
    """
    if not text:
        return None
    # Strip markdown fences first (```json ... ```)
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Fallback: greedy match on outermost braces
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def generate_sentences(
    fragments: list[dict],
    *,
    citekey: str,
    authors: str,
    year: Optional[int],
    outline: list[dict],
    language: str,
    ai: AIProvider,
    klemma_home: Optional[Path] = None,
    project_chain: Optional[list] = None,
) -> SentenceResult:
    """Generate one academic sentence per fragment in ``language``.

    Parameters
    ----------
    fragments : list[dict]
        Each dict: ``{fragment_id, text, citation_intent, assigned_section}``.
    citekey, authors, year :
        Source metadata. ``authors`` is a free-form string; normalized here.
    outline : list[dict]
        ``[{section_id, title, description?}, ...]`` — informational context.
    language : str
        Target language (e.g. ``"Russian"``, ``"English"``).
    ai : AIProvider
        Provider. Must support ``call_with_meta``.

    Returns
    -------
    SentenceResult
        ``sentences`` maps fragment_id → single academic sentence.
        ``failed`` lists fragment_ids the model skipped or returned malformed.
    """
    result = SentenceResult()
    if not fragments:
        return result

    authors_normalized = _normalize_authors(authors)

    if klemma_home is not None:
        prompt_path = resolve_prompt(
            "suggest_sentence.md",
            klemma_home,
            project_chain=project_chain,
        )
    else:
        # Fallback for callers without a project root (e.g. SaaS worker).
        from ..config import _SHIPPED_PROMPTS_DIR

        prompt_path = _SHIPPED_PROMPTS_DIR / "suggest_sentence.md"
    prompt_text = ai.render_prompt(
        prompt_path,
        language=language,
        citekey=citekey,
        year=year if year is not None else "",
        authors_json=json.dumps(authors_normalized, ensure_ascii=False),
        outline=outline or [],
        fragments=fragments,
    )

    system = (
        "You are an academic writing assistant. Produce faithful, "
        "citation-ready sentences. Output only valid JSON."
    )

    logger.info(
        "Generating suggested sentences: citekey=%s count=%d language=%s",
        citekey,
        len(fragments),
        language,
    )

    meta = ai.call_with_meta(system, prompt_text)
    result.model = meta.model or ""
    result.input_tokens = meta.input_tokens or 0
    result.output_tokens = meta.output_tokens or 0

    if meta.text is None:
        # Total failure — no output at all. Report every fragment as failed.
        logger.warning("sentence_generator: AI returned no text (error=%s)", meta.error)
        result.failed = [f["fragment_id"] for f in fragments]
        return result

    parsed = _extract_json_object(meta.text)
    returned_ids: set[str] = set()
    if parsed and isinstance(parsed.get("sentences"), list):
        for item in parsed["sentences"]:
            if not isinstance(item, dict):
                continue
            frag_id = item.get("fragment_id")
            sentence = item.get("text")
            if not frag_id or not isinstance(sentence, str) or not sentence.strip():
                continue
            result.sentences[frag_id] = sentence.strip()
            returned_ids.add(frag_id)
    else:
        logger.warning(
            "sentence_generator: could not parse JSON (first 200 chars: %r)",
            meta.text[:200],
        )

    # Any fragment the model didn't return a valid sentence for is a failure.
    result.failed = [
        f["fragment_id"] for f in fragments if f["fragment_id"] not in returned_ids
    ]
    return result
