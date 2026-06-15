"""Citation integrity verification engine (ADR-018).

Pattern: LLM-as-judge verifier + isolated judge-provider + evidence model
(source_available / search_complete / anchor_found) + dispatch table.

Public API
----------
detect_anchors     — heuristic anchor extraction (no AI)
_parse_claims      — markdown claim extraction with offset-safe masking
verify_claim       — deterministic verifier (quote + numeric-absent)
verify_claim_batch — AI verifier (numeric-drift + definitional)
build_judge_provider — build isolated judge AIProvider
check_citations_file — standalone orchestrator
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from ..ai import AIProvider
    from ..config import KlemmaConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ClaimAnchor:
    kind: Literal["numeric", "definitional", "quote"]
    raw: str
    trigger: str
    start_offset: int
    end_offset: int
    anchor_id: str  # "{start_offset}:{end_offset}"


@dataclass
class Claim:
    sentence: str
    citekey: str
    location: str
    anchors: list[ClaimAnchor]
    start_offset: int
    end_offset: int


@dataclass
class EvidenceBundle:
    claim_sentence: str
    citekey: str
    location: str
    anchor: ClaimAnchor
    passages: list[str]
    source_available: bool
    search_complete: bool  # True only for sidecar (full untruncated PDF text)
    anchor_found: bool


@dataclass
class CitationVerdict:
    citekey: str
    claim_sentence: str
    location: str
    anchor: ClaimAnchor
    severity: Literal["ok", "unverifiable", "soft_warn", "hard_warn", "error"]
    reason: str
    offending_span: str
    ai_used: bool


@dataclass
class BatchResult:
    verdicts: list[CitationVerdict]
    input_tokens: int
    output_tokens: int
    model: Optional[str]
    errors: list[str]


@dataclass
class CitationCheckReport:
    target: str
    verdicts: list[CitationVerdict]
    summary: str
    status: Literal["ok", "degraded", "error"]
    errors: list[str]
    input_tokens: int
    output_tokens: int
    model: Optional[str]


# ---------------------------------------------------------------------------
# Deadline helper
# ---------------------------------------------------------------------------


@dataclass
class _Deadline:
    _end: float = field(repr=False)

    @classmethod
    def from_secs(cls, secs: float) -> "_Deadline":
        return cls(_end=time.monotonic() + secs)

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

_DASH_RE = re.compile(r"[­‐-―−﹘﹣－]")
_SPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """NFKC + dash normalization + whitespace collapse + lowercase."""
    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)
    text = _SPACE_RE.sub(" ", text)
    return text.lower().strip()


# ---------------------------------------------------------------------------
# Anchor detection
# ---------------------------------------------------------------------------

_DEFINITIONAL_TRIGGERS = [
    # Russian
    "— это", "является", "означает", "определяется как", "соответствует",
    "разделяет", "разделяющий", "отделяет", "граница между", "относится к",
    # English
    "corresponds to", "separates", "is defined as", "is described as",
    "refers to", "is understood as",
]

_NUMERIC_RE = re.compile(
    r"(?<![А-Яа-яA-Za-z])"
    r"([-−]?\d{1,6}(?:[.,]\d{1,4})?\s*(?:%|процент|°C|°K|кг|км|м\b|мм)?)"
    r"(?![А-Яа-яA-Za-z\d])",
)

_QUOTE_RE = re.compile(r'[«"](.*?)[»"]', re.DOTALL)
_QUOTE_MIN_WORDS = 5
_QUOTE_MIN_CHARS = 30


def detect_anchors(sentence: str, base_offset: int = 0) -> list[ClaimAnchor]:
    """Detect claim anchors in a sentence with absolute offsets into the source text.

    Short quoted terms (< 5 words / 30 chars) are NOT classified as quote anchors —
    they may still trigger a definitional anchor via trigger-word detection.
    """
    anchors: list[ClaimAnchor] = []
    seen_ids: set[str] = set()

    def _add(anchor: ClaimAnchor) -> None:
        if anchor.anchor_id not in seen_ids:
            seen_ids.add(anchor.anchor_id)
            anchors.append(anchor)

    # Quote anchors (long enough to be verbatim)
    for m in _QUOTE_RE.finditer(sentence):
        content = m.group(1)
        if len(content.split()) >= _QUOTE_MIN_WORDS or len(content) >= _QUOTE_MIN_CHARS:
            start = base_offset + m.start()
            end = base_offset + m.end()
            _add(ClaimAnchor(
                kind="quote",
                raw=m.group(0),
                trigger="quoted_span",
                start_offset=start,
                end_offset=end,
                anchor_id=f"{start}:{end}",
            ))

    # Definitional anchors
    lower = sentence.lower()
    for trigger in _DEFINITIONAL_TRIGGERS:
        idx = lower.find(trigger.lower())
        if idx == -1:
            continue
        raw_start = max(0, idx - 30)
        raw_end = min(len(sentence), idx + len(trigger) + 50)
        start = base_offset + raw_start
        end = base_offset + raw_end
        _add(ClaimAnchor(
            kind="definitional",
            raw=sentence[raw_start:raw_end].strip(),
            trigger=trigger,
            start_offset=start,
            end_offset=end,
            anchor_id=f"{start}:{end}",
        ))

    # Numeric anchors
    for m in _NUMERIC_RE.finditer(sentence):
        raw = m.group(0).strip()
        start = base_offset + m.start()
        end = base_offset + m.end()
        _add(ClaimAnchor(
            kind="numeric",
            raw=raw,
            trigger="numeric_value",
            start_offset=start,
            end_offset=end,
            anchor_id=f"{start}:{end}",
        ))

    return anchors


# ---------------------------------------------------------------------------
# Claim parsing
# ---------------------------------------------------------------------------

_CITE_REF_RE = re.compile(
    r"\[{1,2}"         # [ or [[
    r"(-?@[^\[\]]+)"   # content starting with optional - then @
    r"\]{1,2}"         # ] or ]]
)

_SENT_TERMINATORS = frozenset(".!?;")


def _extract_citekeys_from_ref(content: str) -> list[str]:
    """Extract one or more citekeys from a citation reference content string."""
    parts = re.split(r";\s*", content)
    keys = []
    for part in parts:
        m = re.match(r"\s*-?@([A-Za-z][A-Za-z0-9_:\-]*)", part.strip())
        if m:
            keys.append(m.group(1))
    return keys


def _mask_excluded_regions(text: str) -> str:
    """Replace frontmatter / fenced code / inline code / HTML comments with spaces.

    Preserves string length so that offsets into the original text remain valid.
    """
    buf = list(text)

    def _blank(start: int, end: int) -> None:
        for i in range(start, end):
            buf[i] = " "

    # Frontmatter (must be at file start)
    fm_match = re.match(r"\A---\n.+?\n---\n", text, re.DOTALL)
    if fm_match:
        _blank(0, fm_match.end())

    # Fenced code blocks (``` ... ```)
    for m in re.finditer(r"```[\s\S]*?```", text):
        _blank(m.start(), m.end())

    # Inline code (`...`)
    for m in re.finditer(r"`[^`\n]+`", text):
        _blank(m.start(), m.end())

    # HTML comments (<!-- ... -->)
    for m in re.finditer(r"<!--[\s\S]*?-->", text):
        _blank(m.start(), m.end())

    return "".join(buf)


def _find_sentence_bounds(masked: str, cite_start: int, cite_end: int) -> tuple[int, int]:
    """Find sentence boundaries around a citation reference in masked text."""
    # --- sentence start: scan backward ---
    sent_start = 0
    i = cite_start - 1
    while i >= 0:
        ch = masked[i]
        if ch in _SENT_TERMINATORS:
            sent_start = i + 1
            break
        if ch == "\n":
            # Paragraph break (two consecutive newlines)
            if i > 0 and masked[i - 1] == "\n":
                sent_start = i + 1
                break
            # Next non-newline starts a markdown heading
            j = i + 1
            while j < len(masked) and masked[j] == "\n":
                j += 1
            if j < len(masked) and masked[j] == "#":
                sent_start = i + 1
                break
        i -= 1

    # skip leading whitespace
    while sent_start < cite_start and masked[sent_start] in " \t\n\r":
        sent_start += 1

    # --- sentence end: scan forward ---
    sent_end = len(masked)
    i = cite_end
    while i < len(masked):
        ch = masked[i]
        if ch in _SENT_TERMINATORS:
            sent_end = i + 1
            break
        if ch == "\n":
            if i + 1 < len(masked) and masked[i + 1] == "\n":
                sent_end = i
                break
            j = i + 1
            while j < len(masked) and masked[j] == "\n":
                j += 1
            if j < len(masked) and masked[j] == "#":
                sent_end = i
                break
        i += 1

    return sent_start, sent_end


def _parse_claims(md_text: str) -> list[Claim]:
    """Parse citation claims from markdown text.

    Returns one Claim per (sentence, citekey) pair.
    Regions in frontmatter / code blocks / HTML comments are masked with spaces
    so that offsets remain valid into the original md_text.
    """
    masked = _mask_excluded_regions(md_text)
    claims: list[Claim] = []

    for cite_m in _CITE_REF_RE.finditer(masked):
        content = cite_m.group(1)
        citekeys = _extract_citekeys_from_ref(content)
        if not citekeys:
            continue

        sent_start, sent_end = _find_sentence_bounds(masked, cite_m.start(), cite_m.end())
        raw_sentence = md_text[sent_start:sent_end]
        sentence = raw_sentence.strip()
        if len(sentence) < 10:
            continue

        anchors = detect_anchors(raw_sentence, base_offset=sent_start)

        for ck in citekeys:
            claims.append(Claim(
                sentence=sentence,
                citekey=ck,
                location="",
                anchors=anchors,
                start_offset=sent_start,
                end_offset=sent_end,
            ))

    return claims


# ---------------------------------------------------------------------------
# Evidence resolution
# ---------------------------------------------------------------------------

def _build_passages(source_text: str, anchor_raw: str, n: int = 3) -> list[str]:
    """Return up to n text passages around the anchor in source_text."""
    norm_anchor = _normalize_text(anchor_raw)
    norm_source = _normalize_text(source_text)
    idx = norm_source.find(norm_anchor)

    if idx == -1:
        # Anchor not found — return the start as context
        return [source_text[:800]] if source_text else []

    # Estimate position in original text (norm collapses whitespace, use ratio)
    ratio = len(source_text) / max(len(norm_source), 1)
    approx = int(idx * ratio)
    window_start = max(0, approx - 400)
    window_end = min(len(source_text), approx + 400)
    return [source_text[window_start:window_end]]


def _resolve_evidence(
    claim: Claim,
    anchor: ClaimAnchor,
    *,
    project_root: Path,
    state=None,
    paper_store=None,
    user_library=None,
) -> EvidenceBundle:
    """Resolve source text and build an EvidenceBundle for one anchor.

    Priority: sidecar (search_complete=True) → paper_store raw_text →
    legacy state fragments (both search_complete=False).
    """
    citekey = claim.citekey
    source_text: Optional[str] = None
    search_complete = False

    # 1. Sidecar — full PDF text (ADR-016); _validate_citekey inside guards traversal.
    # ADR-018 exception: sidecar is pure file I/O (no storage layer);
    # same pattern rationale as ADR-008 (shared helpers in skills/).
    try:
        from ..literature.sidecar import read_pdf_sidecar
        sidecar = read_pdf_sidecar(project_root, citekey)
        if sidecar:
            source_text = sidecar
            search_complete = True
    except Exception:
        logger.debug("sidecar lookup failed for citekey=%s", citekey)

    # 2. paper_store raw_text cache (50 K truncated)
    if source_text is None and paper_store is not None and user_library is not None:
        try:
            paper_id = user_library.resolve_paper_id(citekey)
            if paper_id:
                raw = paper_store.get_raw_text(paper_id)
                if raw:
                    source_text = raw
        except Exception:
            logger.debug("paper_store lookup failed for citekey=%s", citekey)

    # 3. Legacy state fragments fallback
    if source_text is None and state is not None:
        try:
            frags = None
            if hasattr(state, "get_fragments"):
                frags = state.get_fragments(citekey) or []
            if frags:
                texts = []
                for f in frags:
                    t = f.get("fragment_text", "") if isinstance(f, dict) else getattr(f, "fragment_text", "")
                    if t:
                        texts.append(t)
                if texts:
                    source_text = "\n".join(texts)
        except Exception:
            logger.debug("state fallback failed for citekey=%s", citekey)

    if not source_text:
        return EvidenceBundle(
            claim_sentence=claim.sentence,
            citekey=citekey,
            location=claim.location,
            anchor=anchor,
            passages=[],
            source_available=False,
            search_complete=False,
            anchor_found=False,
        )

    # Search for anchor in source
    norm_anchor = _normalize_text(anchor.raw)
    norm_source = _normalize_text(source_text)
    anchor_found = bool(norm_anchor and norm_anchor in norm_source)

    passages = _build_passages(source_text, anchor.raw)

    return EvidenceBundle(
        claim_sentence=claim.sentence,
        citekey=citekey,
        location=claim.location,
        anchor=anchor,
        passages=passages,
        source_available=True,
        search_complete=search_complete,
        anchor_found=anchor_found,
    )


# ---------------------------------------------------------------------------
# Deterministic verifier (no AI)
# ---------------------------------------------------------------------------


def _needs_ai_check(bundle: EvidenceBundle) -> bool:
    """True when this bundle requires AI to produce a meaningful verdict."""
    anchor = bundle.anchor
    if anchor.kind == "definitional":
        return True
    if anchor.kind == "numeric" and bundle.anchor_found:
        return True
    return False


def _make_unverifiable(
    bundle: EvidenceBundle,
    reason: str,
    ai_used: bool = False,
) -> CitationVerdict:
    return CitationVerdict(
        citekey=bundle.citekey,
        claim_sentence=bundle.claim_sentence,
        location=bundle.location,
        anchor=bundle.anchor,
        severity="unverifiable",
        reason=reason,
        offending_span="",
        ai_used=ai_used,
    )


def verify_claim(bundle: EvidenceBundle) -> CitationVerdict:
    """Deterministic no-AI verifier. Handles quote and numeric-absent dispatch rows.

    AI-required rows (numeric-present-drift, definitional) are NOT handled here —
    the orchestrator routes those to verify_claim_batch().
    """
    anchor = bundle.anchor

    def _v(severity: str, reason: str, span: str = "") -> CitationVerdict:
        return CitationVerdict(
            citekey=bundle.citekey,
            claim_sentence=bundle.claim_sentence,
            location=bundle.location,
            anchor=anchor,
            severity=severity,
            reason=reason,
            offending_span=span,
            ai_used=False,
        )

    if not bundle.source_available:
        return _v("unverifiable", "source text not available")

    if anchor.kind == "quote":
        # Strip outer quote punctuation for comparison
        raw_stripped = anchor.raw.strip('«»"\'')
        norm_anchor = _normalize_text(raw_stripped)
        source_combined = " ".join(bundle.passages)
        norm_source = _normalize_text(source_combined)
        if norm_anchor and norm_anchor in norm_source:
            return _v("ok", "verbatim quote found in source")
        elif bundle.search_complete:
            return _v("hard_warn", "verbatim quote not found in full source text", anchor.raw)
        else:
            return _v("unverifiable", "verbatim quote not found; source search incomplete", anchor.raw)

    if anchor.kind == "numeric":
        # anchor_found==True means value is present → drift needs AI (should be routed there)
        if not bundle.anchor_found:
            if bundle.search_complete:
                return _v("hard_warn", f"numeric value {anchor.raw!r} absent from full source", anchor.raw)
            else:
                return _v("unverifiable", f"numeric value {anchor.raw!r} not found; source search incomplete", anchor.raw)
        # numeric-present — should be in AI batch; unverifiable here
        return _v("unverifiable", "numeric value present in source; drift check requires AI")

    # definitional → always AI; unverifiable in deterministic path
    return _v("unverifiable", "definitional claim requires AI verification")


# ---------------------------------------------------------------------------
# AI verifier (judge batch)
# ---------------------------------------------------------------------------


def _sanitize_delimiters(text: str) -> str:
    """Strip data-boundary markers from untrusted user/AI text."""
    return text.replace("<<<", "<<").replace(">>>", ">>")


def _map_judge_severity(
    verdict: str,
    contradiction: bool,
    severity_hint: str,
    anchor_kind: str,
    search_complete: bool,
) -> Literal["ok", "unverifiable", "soft_warn", "hard_warn", "error"]:
    valid = {"ok", "unverifiable", "soft_warn", "hard_warn", "error"}

    if anchor_kind == "definitional":
        if contradiction:
            return "hard_warn"
        if verdict in ("unsupported", "not_found", "contradicted"):
            return "soft_warn"  # silence ≠ fabrication
        if severity_hint in valid:
            return severity_hint  # type: ignore[return-value]
        return "soft_warn"

    # numeric-drift
    if severity_hint in valid:
        return severity_hint  # type: ignore[return-value]
    if verdict == "ok":
        return "ok"
    if contradiction or verdict == "contradicted":
        return "hard_warn"
    if verdict in ("unsupported", "not_found"):
        return "soft_warn"
    return "unverifiable"


def verify_claim_batch(
    bundles: list[EvidenceBundle],
    *,
    judge_ai: "AIProvider",
    deadline: _Deadline,
    cfg: "KlemmaConfig",
    prompt_path: Optional[Path],
) -> BatchResult:
    """AI-powered verifier for numeric-drift and definitional anchors.

    Returns BatchResult with token/model metadata for SaaS record_usage.
    Never raises — unexpected errors become unverifiable verdicts.
    """
    ai_cfg = cfg.ai

    def _all_unverifiable(reason: str, errors: list[str] = ()) -> BatchResult:
        return BatchResult(
            verdicts=[_make_unverifiable(b, reason) for b in bundles],
            input_tokens=0,
            output_tokens=0,
            model=None,
            errors=list(errors),
        )

    # Deadline gate (check before call; timeout=0 is falsy in LiteLLM → would disable limit)
    remaining = deadline.remaining()
    if remaining <= 0:
        return _all_unverifiable("deadline exceeded before AI call", ["deadline exceeded"])

    if prompt_path is None or not prompt_path.exists():
        return _all_unverifiable("citation_check.md prompt not found")

    # Build batch payload with size-capped, sanitized fields
    batch_items = []
    for b in bundles:
        claim_text = _sanitize_delimiters(b.claim_sentence)
        if len(claim_text) > ai_cfg.citation_check_max_claim_chars:
            claim_text = claim_text[:ai_cfg.citation_check_max_claim_chars] + " [TRUNCATED]"

        passages = b.passages[:ai_cfg.citation_check_max_passages]
        capped_passages = []
        for p in passages:
            p = _sanitize_delimiters(p)
            if len(p) > ai_cfg.citation_check_max_passage_chars:
                p = p[:ai_cfg.citation_check_max_passage_chars] + " [TRUNCATED]"
            capped_passages.append(p)

        batch_items.append({
            "anchor_id": b.anchor.anchor_id,
            "anchor_raw": _sanitize_delimiters(b.anchor.raw),
            "anchor_kind": b.anchor.kind,
            "claim_sentence": claim_text,
            "passages": capped_passages,
        })

    # Render prompt
    try:
        rendered = judge_ai.render_prompt(
            prompt_path,
            bundles=batch_items,
            max_claim_chars=ai_cfg.citation_check_max_claim_chars,
            max_passage_chars=ai_cfg.citation_check_max_passage_chars,
        )
    except Exception as exc:
        logger.warning("citation_check.md render failed: %s", exc)
        return _all_unverifiable("prompt render failed", [f"render error: {exc}"])

    # Overall prompt size cap
    if len(rendered) > ai_cfg.citation_check_max_prompt_chars:
        return _all_unverifiable(
            "prompt size cap exceeded",
            ["rendered prompt exceeded citation_check_max_prompt_chars"],
        )

    # Make the judge call with tight timeout
    effective_timeout = min(ai_cfg.citation_check_timeout, int(remaining))
    if effective_timeout <= 0:
        return _all_unverifiable("no time remaining", ["no time remaining for AI call"])

    try:
        meta = judge_ai.call_with_meta(
            rendered,
            "Verify the citation claims. Return only the JSON verdict object.",
            max_tokens=ai_cfg.citation_check_max_output_tokens,
            timeout=effective_timeout,
        )
    except Exception as exc:
        logger.exception("judge call_with_meta raised: %s", exc)
        return _all_unverifiable("judge call raised exception", [str(exc)])

    if not meta or not meta.text:
        return BatchResult(
            verdicts=[_make_unverifiable(b, "judge returned empty response") for b in bundles],
            input_tokens=getattr(meta, "input_tokens", 0) or 0,
            output_tokens=getattr(meta, "output_tokens", 0) or 0,
            model=getattr(meta, "model", None),
            errors=["judge returned empty response"],
        )

    if meta.error:
        return BatchResult(
            verdicts=[_make_unverifiable(b, f"judge error: {meta.error}") for b in bundles],
            input_tokens=meta.input_tokens or 0,
            output_tokens=meta.output_tokens or 0,
            model=meta.model,
            errors=[str(meta.error)],
        )

    # Parse JSON envelope
    from ..ai import extract_json
    data = extract_json(meta.text)

    if not data or "verdicts" not in data:
        logger.warning("citation judge malformed JSON (first 200 chars): %s", meta.text[:200])
        return BatchResult(
            verdicts=[_make_unverifiable(b, "malformed judge response") for b in bundles],
            input_tokens=meta.input_tokens or 0,
            output_tokens=meta.output_tokens or 0,
            model=meta.model,
            errors=["malformed JSON from judge"],
        )

    # Map verdicts by anchor_id
    by_id: dict[str, dict] = {}
    for item in data["verdicts"]:
        aid = item.get("anchor_id")
        if aid and aid not in by_id:
            by_id[aid] = item
        elif aid:
            logger.debug("duplicate anchor_id %s in judge response; using first", aid)

    verdicts: list[CitationVerdict] = []
    errors: list[str] = []

    for b in bundles:
        aid = b.anchor.anchor_id
        item = by_id.get(aid)
        if not item:
            verdicts.append(_make_unverifiable(b, "anchor_id missing from judge response", ai_used=True))
            errors.append(f"missing anchor_id {aid} in judge response")
            continue

        severity = _map_judge_severity(
            verdict=item.get("verdict", ""),
            contradiction=bool(item.get("contradiction", False)),
            severity_hint=item.get("severity", ""),
            anchor_kind=b.anchor.kind,
            search_complete=b.search_complete,
        )
        reason = item.get("reason") or "no reason provided"

        verdicts.append(CitationVerdict(
            citekey=b.citekey,
            claim_sentence=b.claim_sentence,
            location=b.location,
            anchor=b.anchor,
            severity=severity,
            reason=reason,
            offending_span=item.get("offending_span", ""),
            ai_used=True,
        ))

    return BatchResult(
        verdicts=verdicts,
        input_tokens=meta.input_tokens or 0,
        output_tokens=meta.output_tokens or 0,
        model=meta.model,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Judge provider
# ---------------------------------------------------------------------------


def _judge_model(ai_cfg: "KlemmaConfig.ai") -> str:  # type: ignore[name-defined]
    """Determine the model string for the citation judge.

    For litellm: override must be provider/model with the SAME provider as cfg.model.
    For claude: bare id accepted. Returns cfg.model on incompatibility.
    """
    override = ai_cfg.citation_check_model
    if not override:
        return ai_cfg.model

    backend = ai_cfg.backend

    if backend == "litellm":
        if "/" not in override:
            logger.warning(
                "citation_check_model %r lacks provider prefix for litellm backend; "
                "falling back to %r",
                override, ai_cfg.model,
            )
            return ai_cfg.model
        override_provider = override.split("/", 1)[0]
        cfg_provider = ai_cfg.model.split("/", 1)[0] if "/" in ai_cfg.model else "openai"
        if override_provider != cfg_provider:
            logger.warning(
                "citation_check_model provider %r != cfg.model provider %r; "
                "cross-provider judge would use wrong API key; falling back to %r",
                override_provider, cfg_provider, ai_cfg.model,
            )
            return ai_cfg.model

    return override


def build_judge_provider(config: "KlemmaConfig") -> Optional["AIProvider"]:
    """Build an isolated AI provider for citation judging (ADR-018).

    CTO RC3: for claude backend, judge is routed through litellm only when
    citation_check_model is explicitly set in 'anthropic/...' format AND an
    anthropic key is available. Otherwise returns None (degraded mode).

    Expected failures (ImportError, missing key, config errors) → return None.
    Unexpected failures re-raised for the caller to handle.
    """
    from ..ai import create_ai

    ai_cfg = config.ai
    judge_model = _judge_model(ai_cfg)
    backend = ai_cfg.backend

    if backend == "claude":
        # CTO RC3: only use litellm fallback with explicit anthropic/... model
        if not judge_model.startswith("anthropic/"):
            logger.warning(
                "build_judge_provider: claude backend requires citation_check_model "
                "in 'anthropic/model-name' format; judge unavailable (degraded)"
            )
            return None

        anthropic_key = (
            ai_cfg._resolved_api_keys.get("anthropic")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not anthropic_key:
            logger.warning(
                "build_judge_provider: no ANTHROPIC_API_KEY for litellm judge fallback"
            )
            return None

        judge_cfg = ai_cfg.model_copy(update={
            "backend": "litellm",
            "model": judge_model,
            "retries": ai_cfg.citation_check_retries,
            "timeout": ai_cfg.citation_check_timeout,
            "json_mode": True,
        })
        # PrivateAttr mutation after model_copy is legal in Pydantic v2 (instance-level, not model-level)
        judge_cfg._resolved_api_keys = {"anthropic": anthropic_key}

    else:
        # litellm / openai backend
        judge_cfg = ai_cfg.model_copy(update={
            "model": judge_model,
            "retries": ai_cfg.citation_check_retries,
            "timeout": ai_cfg.citation_check_timeout,
            "json_mode": True,
        })
        judge_cfg._resolved_api_keys = dict(ai_cfg._resolved_api_keys)  # isolated copy

    try:
        return create_ai(judge_cfg)
    except ImportError as exc:
        logger.warning("build_judge_provider ImportError (litellm not installed?): %s", exc)
        return None
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("build_judge_provider config error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def check_citations_file(
    target_path: Path,
    *,
    config: "KlemmaConfig",
    state=None,
    paper_store=None,
    user_library=None,
    judge_ai: Optional["AIProvider"],
    project_root: Path,
    klemma_home=None,
    project_chain=None,
    use_ai: bool = True,
    fail_on: str = "hard_warn",
) -> CitationCheckReport:
    """Verify citation integrity in a draft markdown file.

    judge_ai must be pre-built by the caller (one judge per command invocation,
    shared across all files). Pass None for no-AI mode.
    Per-file deadline and call-budget are created fresh here.
    """
    # Read target
    try:
        md_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        return CitationCheckReport(
            target=str(target_path),
            verdicts=[],
            summary=f"cannot read file: {exc}",
            status="error",
            errors=[str(exc)],
            input_tokens=0,
            output_tokens=0,
            model=None,
        )

    claims = _parse_claims(md_text)
    if not claims:
        return CitationCheckReport(
            target=str(target_path),
            verdicts=[],
            summary="no citation claims found",
            status="ok",
            errors=[],
            input_tokens=0,
            output_tokens=0,
            model=None,
        )

    # Per-file budget
    ai_cfg = config.ai
    deadline = _Deadline.from_secs(ai_cfg.citation_check_max_wall_clock)
    ai_calls_remaining = ai_cfg.max_ai_calls_per_draft

    # Resolve prompt path once
    prompt_path: Optional[Path] = None
    if use_ai and judge_ai is not None:
        try:
            from ..config import resolve_prompt
            prompt_path = resolve_prompt(
                "citation_check.md",
                klemma_home or Path(""),
                project_chain or [],
            )
        except Exception as exc:
            logger.warning("could not resolve citation_check.md prompt: %s", exc)

    verdicts: list[CitationVerdict] = []
    total_in = 0
    total_out = 0
    report_model: Optional[str] = None
    errors: list[str] = []
    status: Literal["ok", "degraded", "error"] = "ok"

    for claim in claims:
        for anchor in claim.anchors:
            try:
                bundle = _resolve_evidence(
                    claim, anchor,
                    project_root=project_root,
                    state=state,
                    paper_store=paper_store,
                    user_library=user_library,
                )

                if not bundle.source_available:
                    verdicts.append(_make_unverifiable(bundle, "source not available"))
                    continue

                needs_ai = _needs_ai_check(bundle) and use_ai and judge_ai is not None

                if needs_ai:
                    if ai_calls_remaining <= 0 or deadline.remaining() <= 0:
                        verdicts.append(_make_unverifiable(
                            bundle, "AI call budget/deadline exhausted"
                        ))
                        if status == "ok":
                            status = "degraded"
                        continue

                    batch = verify_claim_batch(
                        [bundle],
                        judge_ai=judge_ai,
                        deadline=deadline,
                        cfg=config,
                        prompt_path=prompt_path,
                    )
                    ai_calls_remaining -= 1
                    total_in += batch.input_tokens
                    total_out += batch.output_tokens
                    if batch.model:
                        report_model = batch.model
                    errors.extend(batch.errors)
                    verdicts.extend(batch.verdicts)
                    if batch.errors and status == "ok":
                        status = "degraded"

                else:
                    verdicts.append(verify_claim(bundle))

            except Exception as exc:
                logger.exception(
                    "unexpected error verifying anchor %s for %s",
                    anchor.anchor_id, claim.citekey,
                )
                errors.append(f"unexpected error: {exc}")
                status = "error"

    # Build summary
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.severity] = counts.get(v.severity, 0) + 1
    parts = [
        f"{counts[s]} {s}"
        for s in ("hard_warn", "soft_warn", "unverifiable", "ok")
        if counts.get(s, 0)
    ]
    summary = "; ".join(parts) if parts else "all claims ok"

    return CitationCheckReport(
        target=str(target_path),
        verdicts=verdicts,
        summary=summary,
        status=status,
        errors=errors,
        input_tokens=total_in,
        output_tokens=total_out,
        model=report_model,
    )


# ---------------------------------------------------------------------------
# Inline annotator
# ---------------------------------------------------------------------------


def _safe_comment_payload(s: str, max_len: int = 150) -> str:
    """Sanitize a string for safe use inside an HTML comment.

    HTML comments close on '-->' — we prevent that by:
    - replacing '--' with '-' (eliminates the closing sequence)
    - replacing '>' with ' ' (eliminates the closer char even if '--' slipped through)
    - replacing newlines with spaces
    Order matters: '--' first, then '>'.
    """
    s = s.replace("--", "-").replace(">", " ").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return s[:max_len].strip()


def _annotate_draft(draft_text: str, verdicts: list[CitationVerdict]) -> str:
    """Insert <!-- klemma: ... --> annotations right-to-left into draft_text.

    Annotates soft_warn AND hard_warn/error verdicts.
    Deduplicates by (anchor.start_offset, anchor.end_offset, citekey).
    Inserts at anchor.end_offset in descending order to preserve earlier offsets.
    """
    # Collect unique insertion points
    seen: set[tuple[int, int, str]] = set()
    to_insert: list[tuple[int, str]] = []  # (end_offset, comment)

    for v in verdicts:
        if v.severity not in ("soft_warn", "hard_warn", "error"):
            continue
        key = (v.anchor.start_offset, v.anchor.end_offset, v.citekey)
        if key in seen:
            continue
        seen.add(key)

        safe_ck = _safe_comment_payload(v.citekey, max_len=80)
        if v.severity == "soft_warn":
            comment = f"<!-- klemma: проверь обоснование @{safe_ck} -->"
        else:
            safe_raw = _safe_comment_payload(v.anchor.raw, max_len=80)
            comment = f"<!-- klemma: необоснованный глосс vs @{safe_ck}: {safe_raw} -->"

        to_insert.append((v.anchor.end_offset, comment))

    # Sort descending so right-most insertion doesn't shift earlier offsets
    to_insert.sort(key=lambda x: x[0], reverse=True)

    result = draft_text
    for offset, comment in to_insert:
        # Clamp to text bounds
        offset = min(max(0, offset), len(result))
        result = result[:offset] + comment + result[offset:]

    return result


# ---------------------------------------------------------------------------
# Inline orchestrator (writer/verifier-split, PR 3)
# ---------------------------------------------------------------------------


def check_draft_inline(
    draft_text: str,
    fragments: list[dict],
    rag_fragments: list[dict],
    *,
    config: "KlemmaConfig",
    judge_ai: Optional["AIProvider"],
    project_root: Path,
    klemma_home=None,
    project_chain=None,
    use_ai: bool = True,
) -> "tuple[str, CitationCheckReport]":
    """Verify citation integrity in an in-memory draft (writer/verifier-split, ADR-018).

    Uses in-memory fragments (never re-reads DB/sidecars).
    search_complete=False always — inline path gives soft advisory output.
    Returns (annotated_draft_text, CitationCheckReport).
    Draft text is ALWAYS returned even on engine error.
    """
    all_fragments = list(fragments) + list(rag_fragments)

    try:
        claims = _parse_claims(draft_text)
    except Exception:
        logger.exception("_parse_claims failed in check_draft_inline")
        return draft_text, CitationCheckReport(
            target="inline",
            verdicts=[],
            summary="parse error",
            status="error",
            errors=["_parse_claims raised an unexpected exception"],
            input_tokens=0,
            output_tokens=0,
            model=None,
        )

    if not claims:
        return draft_text, CitationCheckReport(
            target="inline",
            verdicts=[],
            summary="no citation claims found",
            status="ok",
            errors=[],
            input_tokens=0,
            output_tokens=0,
            model=None,
        )

    # Per-draft budget
    ai_cfg = config.ai
    deadline = _Deadline.from_secs(ai_cfg.citation_check_max_wall_clock)
    ai_calls_remaining = ai_cfg.max_ai_calls_per_draft

    # Resolve prompt path once
    prompt_path: Optional[Path] = None
    if use_ai and judge_ai is not None:
        try:
            from ..config import resolve_prompt
            prompt_path = resolve_prompt(
                "citation_check.md",
                klemma_home or Path(""),
                project_chain or [],
            )
        except Exception as exc:
            logger.warning("could not resolve citation_check.md prompt: %s", exc)

    # Build fragment lookup by citekey
    frags_by_ck: dict[str, list[str]] = {}
    for f in all_fragments:
        ck = f.get("source") or f.get("citekey", "")
        text = f.get("text", "") or f.get("fragment_text", "")
        if ck and text:
            frags_by_ck.setdefault(ck, []).append(text)

    verdicts: list[CitationVerdict] = []
    total_in = 0
    total_out = 0
    report_model: Optional[str] = None
    errors: list[str] = []
    status: Literal["ok", "degraded", "error"] = "ok"
    if judge_ai is None and use_ai:
        status = "degraded"

    for claim in claims:
        for anchor in claim.anchors:
            try:
                citekey = claim.citekey
                passages = frags_by_ck.get(citekey, [])
                source_available = bool(passages)

                if not source_available:
                    verdicts.append(CitationVerdict(
                        citekey=citekey,
                        claim_sentence=claim.sentence,
                        location=claim.location,
                        anchor=anchor,
                        severity="unverifiable",
                        reason="source fragments not in prompt context",
                        offending_span="",
                        ai_used=False,
                    ))
                    continue

                # Combine passages into source_text for anchor search
                source_text = "\n".join(passages)
                norm_anchor = _normalize_text(anchor.raw)
                norm_source = _normalize_text(source_text)
                anchor_found = bool(norm_anchor and norm_anchor in norm_source)

                bundle = EvidenceBundle(
                    claim_sentence=claim.sentence,
                    citekey=citekey,
                    location=claim.location,
                    anchor=anchor,
                    passages=passages,
                    source_available=True,
                    search_complete=False,  # inline: never sidecar
                    anchor_found=anchor_found,
                )

                needs_ai = _needs_ai_check(bundle) and use_ai and judge_ai is not None

                if needs_ai:
                    if ai_calls_remaining <= 0 or deadline.remaining() <= 0:
                        verdicts.append(_make_unverifiable(
                            bundle, "AI call budget/deadline exhausted"
                        ))
                        if status == "ok":
                            status = "degraded"
                        continue

                    batch = verify_claim_batch(
                        [bundle],
                        judge_ai=judge_ai,
                        deadline=deadline,
                        cfg=config,
                        prompt_path=prompt_path,
                    )
                    ai_calls_remaining -= 1
                    total_in += batch.input_tokens
                    total_out += batch.output_tokens
                    if batch.model:
                        report_model = batch.model
                    errors.extend(batch.errors)
                    verdicts.extend(batch.verdicts)
                    if batch.errors and status == "ok":
                        status = "degraded"

                else:
                    verdicts.append(verify_claim(bundle))

            except Exception as exc:
                logger.exception(
                    "unexpected error verifying inline anchor %s for %s",
                    anchor.anchor_id, claim.citekey,
                )
                errors.append(f"unexpected error: {exc}")
                status = "error"

    # Build summary
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.severity] = counts.get(v.severity, 0) + 1
    parts = [
        f"{counts[s]} {s}"
        for s in ("hard_warn", "soft_warn", "unverifiable", "ok")
        if counts.get(s, 0)
    ]
    summary = "; ".join(parts) if parts else "all claims ok"

    report = CitationCheckReport(
        target="inline",
        verdicts=verdicts,
        summary=summary,
        status=status,
        errors=errors,
        input_tokens=total_in,
        output_tokens=total_out,
        model=report_model,
    )

    try:
        annotated = _annotate_draft(draft_text, verdicts)
    except Exception:
        logger.exception("_annotate_draft failed; returning unannotated draft")
        annotated = draft_text
        report.status = "error"
        report.errors.append("annotation step raised an unexpected exception")

    return annotated, report
