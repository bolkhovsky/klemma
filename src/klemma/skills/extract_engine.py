"""Pure chunked-extraction engine: pages → ExtractionOutcome, no DB, no vault.

The engine owns everything between "we have the paper text" and "we have a
validated list of fragments with spans": chunking, per-chunk AI calls, JSON
repair, recursive splitting on truncation, budget reservation, verbatim
validation against the full text, text+span deduplication and interval-based
coverage accounting. Callers (CLI ``extract_fragments``, SaaS
``_run_chunked_extraction``) own persistence, run lifecycle and token
accounting — they receive an ``ExtractionOutcome`` and decide what to do.

Design notes (plan C1):

* Coverage is computed as the union of ``[char_start, char_end)`` intervals of
  successful *leaf* chunks against ``[0, len(full_text))``. Overlapping chunks
  and recursive splits therefore can never report more than 100 %.
* A chunk whose answer was truncated (``finish_reason == "max_tokens"``) or
  could not be parsed even after the repair retry is split in half and both
  halves are retried, down to ``min_chunk_chars``; below that the chunk fails.
* Token budget is reserved *before* each call: if the estimated input plus
  the requested output would exceed the remaining budget, the call is not
  made and the chunk fails with ``error="budget"``.
* ``dedup_by_text_and_span`` treats two fragments as duplicates only when
  their normalized full text matches exactly, or fuzzily *and* their spans
  overlap. Two claims sharing the same first 100 characters stay distinct —
  unlike the prefix dedup used by the SaaS worker for backward compatibility.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..ai import normalize_finish_reason  # noqa: F401  (re-exported for callers)
from ..hashing import compute_prompt_hash
from ..literature.models import DowngradeStats, Fragment
from ..text_normalize import normalize, normalize_with_map

logger = logging.getLogger(__name__)

# ── Verbatim validation window ────────────────────────────────────────────
# Offline substring matching — no LLM involved — so the window may be far
# larger than any single extraction chunk. For texts below CAP_SMALL the full
# text is used; above it we cap at CAP_LARGE as a pathological-input backstop
# (difflib with autojunk=False is O(n) memory; 1 MB fits in <50 MB RSS).
# Texts longer than CAP_LARGE are validated only partially and the outcome
# carries ``validation_incomplete=True`` (#382, plan C1).
VERBATIM_VALIDATION_CAP_SMALL = 100_000
VERBATIM_VALIDATION_CAP_LARGE = 1_000_000

_FUZZY_DEDUP_THRESHOLD = 0.95
_PAGE_MARKER_RE = re.compile(r"\[Page (\d+)\]")

_EXTRACT_SYSTEM_PROMPT = (
    "You are a research assistant extracting citation-worthy fragments from scientific papers. "
    "Output only valid JSON with fragments array and key_references array."
)
_REPAIR_SYSTEM_PROMPT = (
    "You receive malformed JSON. Output ONLY a valid JSON object that "
    "preserves every field and value exactly. Do not add commentary, "
    "do not change content, do not drop fragments. Fix only the syntax."
)

_FINISH_MAX_TOKENS = "max_tokens"
_FINISH_UNKNOWN = "unknown"
_FINISH_STOP = "stop"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ChunkOutcome:
    """What happened to one chunk (leaf or split parent)."""

    index: int
    char_start: int
    char_end: int
    status: str  # ok | failed | split
    parent_index: Optional[int] = None
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    fragments: int = 0


@dataclass
class CoverageReport:
    """Interval-union coverage of the full text by successful leaf chunks."""

    total_chars: int
    covered_chars: int
    uncovered: list[tuple[int, int]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if self.total_chars <= 0:
            return 1.0
        return self.covered_chars / self.total_chars

    @property
    def complete(self) -> bool:
        return self.covered_chars >= self.total_chars


@dataclass
class Budget:
    """Per-source token/cost budget. Zero or None means unlimited."""

    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_cost_usd: Optional[float] = None


@dataclass
class ExtractedFragment:
    """A parsed fragment plus its provenance inside ``full_text``.

    ``Fragment`` (project-tier pydantic model) is intentionally not extended:
    vault/annotate code consumes it and must stay untouched.
    """

    fragment: Fragment
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    source_locator: Optional[str] = None
    verbatim_status: str = "unverified"  # confirmed | fuzzy | downgraded | unverified | unclaimed
    chunk_index: int = 0


@dataclass
class ExtractionOutcome:
    """Everything the engine learned about one source. No persistence here."""

    fragments: list[ExtractedFragment]
    key_refs: list[dict]
    summary: str
    notes: dict
    chunks: list[ChunkOutcome]
    coverage: CoverageReport
    prompt_hash: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    failed_chunks: int = 0
    leaf_chunks: int = 0
    validation_incomplete: bool = False
    downgrade_stats: DowngradeStats = field(default_factory=DowngradeStats)
    error: Optional[str] = None
    full_text_length: int = 0

    @property
    def plain_fragments(self) -> list[Fragment]:
        return [ef.fragment for ef in self.fragments]

    @property
    def is_partial(self) -> bool:
        return self.failed_chunks > 0 or not self.coverage.complete


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


# Fuzzy-match rescue threshold. Fragments whose AI-claimed verbatim text fails
# an exact substring check but matches a window of the paper at this ratio or
# above keep `verbatim=true` with a logged warning — this covers PDF extraction
# noise (OCR char swaps, dropped diacritics) without giving cover to
# fabrication. Below this ratio, the fragment is downgraded to
# `verbatim=false`. Revisit after dogfooding the rescue count distribution.
_FUZZY_RESCUE_THRESHOLD = 0.95


def validate_verbatim_fragments(
    fragments: list[Fragment],
    pdf_text: str,
    source_id: str,
) -> DowngradeStats:
    """Enforce the `verbatim=true` claim against the paper text.

    Two-stage match: (1) exact substring after NFKC + PDF-noise normalization;
    (2) difflib ratio fallback for OCR/extractor artifacts. Below the fuzzy
    threshold, flip the flag to `false` instead of dropping the fragment —
    a paraphrase is still useful, we just don't let it masquerade as a quote.

    Caller must pass the full normalized PDF text. Under chunked extraction,
    `process_source` / `reprocess_paper` build it from `extract_pages()` and
    cap it via ``VERBATIM_VALIDATION_CAP_LARGE`` before passing here.
    """
    stats = DowngradeStats()
    if not fragments:
        return stats

    norm_pdf = normalize(pdf_text)
    if not norm_pdf:
        # Nothing to validate against — leave flags as-is and warn once.
        logger.warning(
            "verbatim validator: empty normalized pdf_text for %s; skipping",
            source_id,
        )
        return stats

    for frag in fragments:
        if not frag.verbatim:
            continue  # paraphrases are unverifiable by substring — out of scope
        stats.verbatim_claimed += 1

        norm_frag = normalize(frag.text)
        if not norm_frag:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s): empty normalized fragment", source_id,
            )
            continue

        if norm_frag in norm_pdf:
            stats.verbatim_confirmed += 1
            continue

        # Stage 2: fuzzy rescue against a sliding window sized to the fragment.
        # SequenceMatcher.find_longest_match on the full text is O(n) and fast
        # enough at 50K chars × a handful of fragments; cheaper than chopping
        # windows manually and avoids boundary-miss edge cases.
        matcher = difflib.SequenceMatcher(None, norm_frag, norm_pdf, autojunk=False)
        match = matcher.find_longest_match(0, len(norm_frag), 0, len(norm_pdf))
        if match.size == 0:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s, substring_match_failed): %s…",
                source_id, norm_frag[:80],
            )
            continue

        # Align the window so the fragment-start (position 0) lines up with
        # the best-match anchor in the PDF. Without this, noise near the
        # fragment's start pushes the anchor forward and the window
        # mis-aligns, under-reporting the true similarity.
        window_start = max(0, match.b - match.a)
        window = norm_pdf[window_start : window_start + len(norm_frag)]
        ratio = difflib.SequenceMatcher(None, norm_frag, window, autojunk=False).ratio()
        if ratio >= _FUZZY_RESCUE_THRESHOLD:
            stats.fuzzy_rescued += 1
            logger.info(
                "verbatim fuzzy-rescue (%s, ratio=%.3f): %s… ↔ %s…",
                source_id, ratio, norm_frag[:60], window[:60],
            )
        else:
            frag.verbatim = False
            stats.downgraded += 1
            logger.warning(
                "verbatim downgrade (%s, fuzzy_match_below_threshold:%.3f): %s…",
                source_id, ratio, norm_frag[:80],
            )

    return stats


def _raw_span(source_text: str, idx_map: list[int], a: int, b: int) -> tuple[int, int]:
    """Translate a normalized-space half-open span [a, b) into raw coordinates.

    The end is the raw index right after the last matched char's combining
    sequence, so spans never cut a base char away from its combining marks.
    """
    start = idx_map[a]
    end = idx_map[b - 1] + 1
    while end < len(source_text) and unicodedata.combining(source_text[end]):
        end += 1
    return start, end


def locate_fragment_span(
    fragment_text: str,
    source_text: str,
) -> tuple[int, int] | None:
    """Locate a fragment inside the raw source text; return its span or None.

    Match happens in normalized space (same pipeline as
    ``validate_verbatim_fragments``: exact substring first, then the difflib
    window rescue at ``_FUZZY_RESCUE_THRESHOLD``), and the hit is mapped back
    into raw ``source_text`` coordinates via ``normalize_with_map`` — so the
    returned span indexes directly into the sidecar canonical text.
    """
    norm_frag = normalize(fragment_text)
    norm_src, idx_map = normalize_with_map(source_text)
    if not norm_frag or not norm_src:
        return None

    pos = norm_src.find(norm_frag)
    if pos >= 0:
        return _raw_span(source_text, idx_map, pos, pos + len(norm_frag))

    # Fuzzy rescue — mirrors the window logic in validate_verbatim_fragments.
    matcher = difflib.SequenceMatcher(None, norm_frag, norm_src, autojunk=False)
    match = matcher.find_longest_match(0, len(norm_frag), 0, len(norm_src))
    if match.size == 0:
        return None
    window_start = max(0, match.b - match.a)
    window_end = min(window_start + len(norm_frag), len(norm_src))
    window = norm_src[window_start:window_end]
    ratio = difflib.SequenceMatcher(None, norm_frag, window, autojunk=False).ratio()
    if ratio < _FUZZY_RESCUE_THRESHOLD:
        return None
    return _raw_span(source_text, idx_map, window_start, window_end)



def build_full_text(pages: list[str]) -> str:
    """Join pages with ``[Page N]`` markers — identical to the chunk builder's
    internal string, so chunk offsets index into this text."""
    return "\n\n".join(f"[Page {i + 1}]\n{text}" for i, text in enumerate(pages))


def compute_coverage(chunks: list[ChunkOutcome], total_chars: int) -> CoverageReport:
    """Union of ``[char_start, char_end)`` over ``status == "ok"`` chunks."""
    intervals = sorted(
        (c.char_start, c.char_end) for c in chunks if c.status == "ok" and c.char_end > c.char_start
    )
    merged: list[list[int]] = []
    for a, b in intervals:
        a = max(0, a)
        b = min(total_chars, b)
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    covered = sum(b - a for a, b in merged)
    uncovered: list[tuple[int, int]] = []
    cursor = 0
    for a, b in merged:
        if a > cursor:
            uncovered.append((cursor, a))
        cursor = b
    if cursor < total_chars:
        uncovered.append((cursor, total_chars))
    return CoverageReport(total_chars=total_chars, covered_chars=covered, uncovered=uncovered)


def estimate_cost_usd(
    model: str, tokens_in: int, tokens_out: int, pricing: Optional[dict[str, dict]]
) -> Optional[float]:
    """Look up ``pricing[model] = {"input": $/1M, "output": $/1M}``.

    The lookup tolerates a provider prefix (``anthropic/claude-x`` matches
    ``claude-x``) and returns None when the price is unknown.
    """
    if not pricing or not model:
        return None
    candidates = [model]
    if "/" in model:
        candidates.append(model.split("/", 1)[1])
    for key in candidates:
        entry = pricing.get(key)
        if entry and "input" in entry and "output" in entry:
            return tokens_in / 1e6 * float(entry["input"]) + tokens_out / 1e6 * float(
                entry["output"]
            )
    return None


def _spans_overlap(a: ExtractedFragment, b: ExtractedFragment) -> bool:
    if a.char_start is None or b.char_start is None:
        return False
    return a.char_start < (b.char_end or 0) and b.char_start < (a.char_end or 0)


def dedup_by_text_and_span(fragments: list[ExtractedFragment]) -> list[ExtractedFragment]:
    """Drop later fragments that repeat an earlier one.

    Exact rule: identical NFKC-normalized full text. Fuzzy rule: difflib ratio
    ≥ 0.95 *and* overlapping spans. Fragments without a span are compared by
    exact text only — a shared prefix is never enough.
    """
    kept: list[ExtractedFragment] = []
    seen_norm: dict[str, ExtractedFragment] = {}
    for ef in fragments:
        norm = normalize(ef.fragment.text)
        if norm in seen_norm:
            continue
        duplicate = False
        if ef.char_start is not None:
            for other in kept:
                if not _spans_overlap(ef, other):
                    continue
                other_norm = normalize(other.fragment.text)
                if abs(len(other_norm) - len(norm)) > max(len(norm), len(other_norm)) * 0.2:
                    continue
                ratio = difflib.SequenceMatcher(None, norm, other_norm, autojunk=False).ratio()
                if ratio >= _FUZZY_DEDUP_THRESHOLD:
                    duplicate = True
                    break
        if duplicate:
            continue
        seen_norm[norm] = ef
        kept.append(ef)
    return kept


def _page_at(full_text: str, offset: int) -> int:
    page = 1
    for m in _PAGE_MARKER_RE.finditer(full_text, 0, max(0, offset) + 1):
        page = int(m.group(1))
    return page


def _make_child_chunk(full_text: str, start: int, end: int, index: int):
    """Build a ChunkRecord for ``full_text[start:end]`` with page grounding."""
    from ..literature import pdf as _pdf

    text = full_text[start:end]
    active_page = _page_at(full_text, start)
    if not text.startswith("[Page "):
        text = f"[Page {active_page}]\n{text}"
    inner = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(full_text, start, end)]
    return _pdf.ChunkRecord(
        index=index,
        text=text,
        page_start=active_page,
        page_end=max(inner) if inner else active_page,
        char_start=start,
        char_end=end,
    )


def _parse_fragments(data: dict, chunk_index: int) -> list[ExtractedFragment]:
    out: list[ExtractedFragment] = []
    for f_data in data.get("fragments", []) or []:
        if not isinstance(f_data, dict):
            continue
        text = str(f_data.get("text", "") or "").strip()
        if not text:
            continue
        try:
            relevance = int(f_data.get("relevance", 3) or 3)
        except (TypeError, ValueError):
            relevance = 3
        try:
            fragment = Fragment(
                text=text,
                type=f_data.get("type", "key_idea") or "key_idea",
                chapter=f_data.get("chapter") if isinstance(f_data.get("chapter"), int) else None,
                section=(str(f_data.get("section")).strip() or None)
                if f_data.get("section") is not None
                else None,
                relevance=max(1, min(5, relevance)),
                usage_hint=str(f_data.get("usage_hint", "") or ""),
                page=f_data.get("page") if isinstance(f_data.get("page"), int) else None,
                citation_intent=f_data.get("citation_intent"),
                verbatim=bool(f_data.get("verbatim", False)),
            )
        except Exception as e:  # pydantic validation (e.g. bad citation_intent)
            logger.warning("Invalid fragment skipped: %s", e)
            try:
                fragment = Fragment(
                    text=text,
                    type=f_data.get("type", "key_idea") or "key_idea",
                    relevance=max(1, min(5, relevance)),
                    usage_hint=str(f_data.get("usage_hint", "") or ""),
                    page=f_data.get("page") if isinstance(f_data.get("page"), int) else None,
                    verbatim=bool(f_data.get("verbatim", False)),
                )
            except Exception:
                continue
        out.append(
            ExtractedFragment(
                fragment=fragment,
                chunk_index=chunk_index,
                verbatim_status="unverified" if fragment.verbatim else "unclaimed",
            )
        )
    return out


def _merge_notes(target: dict, incoming: Any) -> None:
    """Accumulate per-chunk ``notes`` (lists concatenated, scalars kept first)."""
    if not isinstance(incoming, dict):
        return
    for key, value in incoming.items():
        if isinstance(value, list):
            target.setdefault(key, []).extend(value)
        elif key not in target:
            target[key] = value


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def extract_from_pages(
    pages: Optional[list[str]],
    entry,
    prompt_path: Path,
    prompt_vars: dict,
    ai,
    *,
    text: Optional[str] = None,
    chunks: Optional[list] = None,
    chunk_size: int = 25_000,
    overlap: int = 2_000,
    min_chunk_chars: int = 4_000,
    max_tokens_cap: int = 8_192,
    mode: str = "standard",
    budget: Optional[Budget] = None,
    model_override: Optional[str] = None,
    pricing: Optional[dict[str, dict]] = None,
    system_prompt: str = _EXTRACT_SYSTEM_PROMPT,
    on_call: Optional[Callable[[Any, str], None]] = None,
    full_text: Optional[str] = None,
) -> ExtractionOutcome:
    """Run chunked extraction over a paper and return a validated outcome.

    Exactly one of ``pages``, ``text`` or ``chunks`` drives chunking:
    ``pages`` → ``build_chunks_from_pages`` (looked up on the
    ``klemma.literature.pdf`` module at call time so tests can patch it);
    ``text`` → a single chunk (online sources); ``chunks`` → prebuilt
    ``ChunkRecord`` list (SaaS worker, tests).

    ``on_call(result, operation)`` is invoked after every AI call with the raw
    ``AICallResult`` and ``"extract"`` | ``"repair"`` — the SaaS wrapper uses
    it for token accounting. The engine never touches a database.
    """
    from ..ai import extract_json
    from ..literature import pdf as _pdf

    if chunks is not None:
        work = list(chunks)
        source_text = full_text if full_text else "\n\n".join(c.text for c in work)
    elif pages:
        source_text = full_text if full_text else build_full_text(pages)
        work = list(_pdf.build_chunks_from_pages(pages, chunk_size=chunk_size, overlap=overlap))
    else:
        source_text = full_text if full_text else (text or "")
        work = [
            _pdf.ChunkRecord(
                index=0,
                text=source_text,
                page_start=1,
                page_end=1,
                char_start=0,
                char_end=len(source_text),
            )
        ]

    total_chars = len(source_text)
    budget = budget or Budget()
    model = model_override or getattr(ai, "model", "") or ""
    try:
        prompt_hash = compute_prompt_hash(Path(prompt_path).read_text(encoding="utf-8"))
    except (OSError, TypeError):
        prompt_hash = compute_prompt_hash(str(prompt_path))

    outcomes: list[ChunkOutcome] = []
    fragments: list[ExtractedFragment] = []
    key_refs: list[dict] = []
    notes: dict = {}
    summaries: list[str] = []
    tokens_in = tokens_out = 0
    next_index = max((c.index for c in work), default=-1) + 1
    engine_error: Optional[str] = None
    budget_exhausted = False
    first_call = True

    queue = [(c, None) for c in work]  # (chunk, parent_index)
    chunk_total_hint = len(work)

    while queue:
        chunk, parent_index = queue.pop(0)
        outcome = ChunkOutcome(
            index=chunk.index,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            status="failed",
            parent_index=parent_index,
        )
        outcomes.append(outcome)

        if budget_exhausted:
            outcome.error = "budget"
            continue

        user_prompt = ai.render_prompt(
            prompt_path,
            title=entry.title or "Unknown",
            authors=entry.authors_str,
            year=entry.year or "Unknown",
            journal=entry.container_title or "N/A",
            doi=entry.DOI or "N/A",
            abstract=entry.abstract or "Not available",
            pdf_text=chunk.text,
            chunk_index=chunk.index,
            chunk_total=chunk_total_hint,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            **prompt_vars,
        )
        max_tokens = max(2048, min(max_tokens_cap, len(chunk.text) // 4))

        # Reserve budget before the call: estimated input + requested output.
        est_in = (len(user_prompt) + len(system_prompt)) // 3
        if budget.max_input_tokens and tokens_in + est_in > budget.max_input_tokens:
            outcome.error = "budget"
            budget_exhausted = True
            continue
        if budget.max_output_tokens and tokens_out + max_tokens > budget.max_output_tokens:
            outcome.error = "budget"
            budget_exhausted = True
            continue
        if budget.max_cost_usd is not None:
            projected = estimate_cost_usd(model, tokens_in + est_in, tokens_out + max_tokens, pricing)
            if projected is not None and projected > budget.max_cost_usd:
                outcome.error = "budget"
                budget_exhausted = True
                continue

        result = ai.call_with_meta(
            system_prompt, user_prompt, max_tokens=max_tokens, model_override=model_override
        )
        if on_call is not None:
            on_call(result, "extract")
        tin = int(getattr(result, "input_tokens", 0) or 0) if result else 0
        tout = int(getattr(result, "output_tokens", 0) or 0) if result else 0
        tokens_in += tin
        tokens_out += tout
        outcome.tokens_in += tin
        outcome.tokens_out += tout
        finish = normalize_finish_reason(getattr(result, "finish_reason", None))
        outcome.finish_reason = finish

        if mode == "exhaustive" and first_call and finish == _FINISH_UNKNOWN:
            engine_error = "backend does not report finish_reason; refuse exhaustive mode"
            outcome.error = engine_error
            logger.error("%s: %s", getattr(entry, "id", "?"), engine_error)
            break
        first_call = False

        if not result or not getattr(result, "text", None):
            outcome.error = getattr(result, "error", None) or "no response"
            logger.warning(
                "Chunk %d returned no AI response for %s — %s",
                chunk.index, getattr(entry, "id", "?"), outcome.error,
            )
            continue

        data = extract_json(result.text)
        if not data:
            repair = ai.call_with_meta(
                _REPAIR_SYSTEM_PROMPT,
                f"Repair this malformed JSON:\n\n{result.text}",
                max_tokens=min(max_tokens_cap, max_tokens * 2),
                model_override=model_override,
            )
            if on_call is not None:
                on_call(repair, "repair")
            if repair:
                rin = int(getattr(repair, "input_tokens", 0) or 0)
                rout = int(getattr(repair, "output_tokens", 0) or 0)
                tokens_in += rin
                tokens_out += rout
                outcome.tokens_in += rin
                outcome.tokens_out += rout
                if getattr(repair, "text", None):
                    data = extract_json(repair.text)
                    if data:
                        logger.info(
                            "Chunk %d: AI repair retry recovered JSON for %s",
                            chunk.index, getattr(entry, "id", "?"),
                        )

        truncated = finish == _FINISH_MAX_TOKENS
        if data and not truncated:
            outcome.status = "ok"
            parsed = _parse_fragments(data, chunk.index)
            outcome.fragments = len(parsed)
            fragments.extend(parsed)
            refs = data.get("key_references")
            if isinstance(refs, list):
                key_refs.extend(r for r in refs if isinstance(r, dict))
            summary = data.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())
            _merge_notes(notes, data.get("notes"))
            continue

        # Truncated or unparseable → split in half if still large enough.
        span = chunk.char_end - chunk.char_start
        if span >= 2 * min_chunk_chars:
            mid = chunk.char_start + span // 2
            left = _make_child_chunk(source_text, chunk.char_start, mid, next_index)
            right = _make_child_chunk(source_text, mid, chunk.char_end, next_index + 1)
            next_index += 2
            outcome.status = "split"
            outcome.error = "truncated" if truncated else "unparseable"
            queue.insert(0, (right, chunk.index))
            queue.insert(0, (left, chunk.index))
            logger.info(
                "Chunk %d (%s) split into %d/%d for %s",
                chunk.index, outcome.error, left.index, right.index, getattr(entry, "id", "?"),
            )
            continue

        outcome.error = "truncated" if truncated else "unparseable"
        logger.warning(
            "Chunk %d failed (%s) for %s — below min_chunk_chars, giving up",
            chunk.index, outcome.error, getattr(entry, "id", "?"),
        )

    # ── Post-processing (pure) ────────────────────────────────────────────
    failed = sum(1 for o in outcomes if o.status == "failed")
    leaves = sum(1 for o in outcomes if o.status != "split")

    validation_incomplete = False
    downgrade_stats = DowngradeStats()
    if fragments:
        if total_chars > VERBATIM_VALIDATION_CAP_LARGE:
            validation_text = source_text[:VERBATIM_VALIDATION_CAP_LARGE]
            validation_incomplete = True
            logger.warning(
                "verbatim validator (%s): %d chars truncated to %d; validation incomplete",
                getattr(entry, "id", "?"), total_chars, VERBATIM_VALIDATION_CAP_LARGE,
            )
        else:
            validation_text = source_text
        plain = [ef.fragment for ef in fragments]
        claimed = [ef.fragment.verbatim for ef in fragments]
        downgrade_stats = validate_verbatim_fragments(plain, validation_text, getattr(entry, "id", "?"))
        for ef, was_claimed in zip(fragments, claimed):
            if not was_claimed:
                ef.verbatim_status = "unclaimed"
            elif ef.fragment.verbatim:
                ef.verbatim_status = "confirmed"
            else:
                ef.verbatim_status = "downgraded"
            span_hit = locate_fragment_span(ef.fragment.text, validation_text)
            if span_hit:
                ef.char_start, ef.char_end = span_hit
                if ef.fragment.page is None:
                    ef.fragment.page = _page_at(source_text, ef.char_start)
        fragments = dedup_by_text_and_span(fragments)

    coverage = compute_coverage(outcomes, total_chars)
    cost = estimate_cost_usd(model, tokens_in, tokens_out, pricing)

    return ExtractionOutcome(
        fragments=fragments,
        key_refs=key_refs,
        summary=max(summaries, key=len) if summaries else "",
        notes=notes,
        chunks=outcomes,
        coverage=coverage,
        prompt_hash=prompt_hash,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        failed_chunks=failed,
        leaf_chunks=leaves,
        validation_incomplete=validation_incomplete,
        downgrade_stats=downgrade_stats,
        error=engine_error,
        full_text_length=total_chars,
    )
