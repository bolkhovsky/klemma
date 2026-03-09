"""Contextual research advisor — methodology-driven heuristics (#123).

Zero AI calls. Thresholds derived from 21 methodology papers:
- Pautasso 2013: source adequacy (15-30/chapter, 5-10/subsection)
- Cohan 2019: citation intent balance (<70% background = healthy)
- Kallestinova 2011: writing readiness (>10 sources + fragments + intents)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Heuristic thresholds (from methodology papers) ---

SOURCE_ADEQUACY_CHAPTER: tuple[int, int] = (15, 30)  # Pautasso 2013
SOURCE_ADEQUACY_SUBSECTION: tuple[int, int] = (5, 10)  # Pautasso 2013
INTENT_BALANCE_THRESHOLD: float = 0.7  # Cohan 2019: <70% background = healthy
WRITING_READINESS_MIN_SOURCES: int = 10  # Kallestinova 2011
SATURATION_THRESHOLD: int = 30  # above this, stop adding


@dataclass
class CoachFinding:
    """Single actionable finding from coach analysis."""

    category: str  # adequacy, intent_balance, writing_readiness, saturation, gap_priority
    section: str | None  # section ID or None for project-wide
    message: str  # human-readable recommendation
    severity: str  # info, warning, action


@dataclass
class CoachReport:
    """Structured coach analysis results."""

    findings: list[CoachFinding] = field(default_factory=list)
    section: str | None = None  # None = project-wide health check


def analyze_section(
    section: str,
    source_count: int,
    level: str,  # "chapter" or "subsection"
    intent_counts: dict[str, int],  # {background: N, method: N, ...}
    fragment_count: int,
    has_draft: bool,
) -> list[CoachFinding]:
    """Analyze a single section and return actionable findings."""
    findings: list[CoachFinding] = []
    min_src, max_src = (
        SOURCE_ADEQUACY_CHAPTER if level == "chapter" else SOURCE_ADEQUACY_SUBSECTION
    )

    # 1. Source adequacy (Pautasso 2013)
    if source_count < min_src:
        findings.append(
            CoachFinding(
                category="adequacy",
                section=section,
                message=(
                    f"Section {section} has {source_count} sources "
                    f"(recommended: {min_src}\u2013{max_src})"
                ),
                severity="warning",
            )
        )
    elif source_count > SATURATION_THRESHOLD:
        findings.append(
            CoachFinding(
                category="saturation",
                section=section,
                message=(
                    f"Section {section} has {source_count} sources \u2014 "
                    f"stop adding, start writing"
                ),
                severity="action",
            )
        )

    # 2. Citation intent balance (Cohan 2019)
    total_intents = sum(intent_counts.values())
    if total_intents > 0:
        bg_ratio = intent_counts.get("background", 0) / total_intents
        if bg_ratio > INTENT_BALANCE_THRESHOLD:
            pct = int(bg_ratio * 100)
            findings.append(
                CoachFinding(
                    category="intent_balance",
                    section=section,
                    message=(
                        f"Section {section}: {pct}% background citations \u2014 "
                        f"need more method/result comparisons"
                    ),
                    severity="warning",
                )
            )

    # 3. Writing readiness (Kallestinova 2011)
    has_intents = any(
        intent_counts.get(k, 0) > 0 for k in ("method", "result_comparison")
    )
    if (
        source_count >= WRITING_READINESS_MIN_SOURCES
        and fragment_count > 0
        and has_intents
        and not has_draft
    ):
        findings.append(
            CoachFinding(
                category="writing_readiness",
                section=section,
                message=(
                    f"Section {section} ready to draft: "
                    f"{source_count} sources, {fragment_count} fragments"
                ),
                severity="info",
            )
        )

    return findings


def analyze_project(
    coverage_stats: dict,
    intent_coverage: dict[str, dict[str, int]],
    fragment_stats: dict,
    gap_summary: dict,
    section_levels: dict[str, str],  # {section: "chapter"|"subsection"}
    drafts: set[str],  # set of section IDs with existing drafts
) -> CoachReport:
    """Project-wide health check -- analyzes all sections + ref-gaps."""
    findings: list[CoachFinding] = []

    sections = coverage_stats.get("sections", {})
    for sec, count in sorted(sections.items()):
        level = section_levels.get(sec, "subsection")
        intents = intent_coverage.get(sec, {})
        frag_count = sum(intents.values()) if intents else 0
        has_draft = sec in drafts
        findings.extend(
            analyze_section(
                section=sec,
                source_count=count,
                level=level,
                intent_counts=intents,
                fragment_count=frag_count,
                has_draft=has_draft,
            )
        )

    # Ref-gap prioritization
    open_count = gap_summary.get("open_count", 0)
    top_ref = gap_summary.get("top_ref")
    top_count = gap_summary.get("top_count", 0)
    if open_count > 0 and top_ref:
        findings.append(
            CoachFinding(
                category="gap_priority",
                section=None,
                message=(
                    f"Resolve {top_ref} \u2014 cited {top_count}\u00d7 across sources "
                    f"({open_count} open gaps total)"
                ),
                severity="action",
            )
        )

    return CoachReport(findings=findings)


def coach_section_hint(
    section: str,
    source_count: int,
    level: str,
    intent_counts: dict[str, int],
    fragment_count: int,
    has_draft: bool,
) -> str | None:
    """Generate a 1-line hint for a section, or None if nothing to say.

    Used by ``add``, ``draft``, ``research`` for inline guidance.
    """
    findings = analyze_section(
        section=section,
        source_count=source_count,
        level=level,
        intent_counts=intent_counts,
        fragment_count=fragment_count,
        has_draft=has_draft,
    )
    if not findings:
        return None

    priority = {"action": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: priority.get(f.severity, 3))
    return findings[0].message
