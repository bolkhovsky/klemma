"""Pydantic models for sources, annotations, and fragments."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Author(BaseModel):
    family: str = ""
    given: Optional[str] = None
    literal: Optional[str] = None

    @property
    def display_name(self) -> str:
        if self.literal:
            return self.literal
        parts = [self.given, self.family] if self.given else [self.family]
        return " ".join(filter(None, parts))


class ZoteroEntry(BaseModel):
    """Single entry from Zotero library."""

    id: str
    type: str = "article"
    title: Optional[str] = None
    abstract: Optional[str] = Field(None, alias="abstractNote")
    author: list[Author] = Field(default_factory=list)
    issued: Optional[dict] = None
    container_title: Optional[str] = Field(None, alias="container-title")
    DOI: Optional[str] = None
    URL: Optional[str] = None
    language: Optional[str] = None
    page: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    keywords: Optional[str] = None
    pdf_path: Optional[str] = None
    item_key: Optional[str] = None  # Zotero internal key (immutable, survives citekey renames)

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @property
    def year(self) -> Optional[int]:
        if self.issued and "date-parts" in self.issued:
            parts = self.issued.get("date-parts", [[]])
            if parts and parts[0]:
                try:
                    return int(parts[0][0])
                except (ValueError, IndexError):
                    pass
        return None

    @property
    def authors_str(self) -> str:
        if not self.author:
            return "Unknown"
        names = [a.display_name for a in self.author[:3]]
        result = ", ".join(names)
        if len(self.author) > 3:
            result += " et al."
        return result

    @property
    def citation(self) -> str:
        first_author = self.author[0].family if self.author else "Unknown"
        year = self.year or "n.d."
        return f"{first_author}, {year}"


class DissertationRelevance(BaseModel):
    primary_chapter: int = Field(1, ge=1, le=4)
    primary_section: str = "1.1"
    relevance_nr1: int = Field(0, ge=0, le=5)
    relevance_nr2: int = Field(0, ge=0, le=5)
    supports_tasks: list[int] = Field(default_factory=list)
    rationale: str = ""


class Quote(BaseModel):
    text: str
    page: Optional[int] = None
    chapter_fit: int = Field(1, ge=1, le=4)
    usage: str = "evidence"


class AnnotationResult(BaseModel):
    """Claude annotation for a source."""

    summary: str = ""
    methodology: str = ""
    key_findings: list[str] = Field(default_factory=list)
    relevance_to_dissertation: str = ""
    suggested_tags: list[str] = Field(default_factory=list)
    quality_score: int = Field(3, ge=1, le=5)
    quotes: list[str] = Field(default_factory=list)
    dissertation_relevance: Optional[DissertationRelevance] = None
    chapters: list[int] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    citation_priority: str = "medium"
    usage_types: list[str] = Field(default_factory=list)
    key_quotes: list[Quote] = Field(default_factory=list)
    related_entries: list[str] = Field(default_factory=list)


class Fragment(BaseModel):
    """Citation fragment extracted from a PDF."""

    text: str
    type: str = "key_idea"  # quote/methodology/result/conclusion/definition/key_idea
    chapter: Optional[int] = None
    section: Optional[str] = None
    relevance: int = Field(3, ge=1, le=5)
    usage_hint: str = ""
    page: Optional[int] = None
    citation_intent: Optional[
        Literal[
            "background", "method", "result_comparison",
            "extends", "contrasts", "uses_data",
        ]
    ] = None
    verbatim: bool = False


class DowngradeStats(BaseModel):
    """Counts from the verbatim validator — surfaced to CLI + SaaS job result.

    Fragments the AI claimed as verbatim but whose text isn't a substring of
    the paper are downgraded to ``verbatim=false`` (not rejected — paraphrases
    are still useful; we just refuse to let the AI lie about quotation).
    """

    verbatim_claimed: int = 0
    verbatim_confirmed: int = 0  # exact substring match after normalization
    fuzzy_rescued: int = 0  # kept as verbatim via difflib ratio ≥ threshold
    downgraded: int = 0  # flipped to verbatim=false

    def as_dict(self) -> dict[str, int]:
        return {
            "verbatim_claimed": self.verbatim_claimed,
            "verbatim_confirmed": self.verbatim_confirmed,
            "fuzzy_rescued": self.fuzzy_rescued,
            "downgraded": self.downgraded,
        }


class ExtractionResult(BaseModel):
    """Result of fragment extraction from a source."""

    source_id: str
    fragments: list[Fragment] = Field(default_factory=list)
    summary: str = ""
    extracted_at: datetime = Field(default_factory=datetime.now)
    downgrade_stats: DowngradeStats = Field(default_factory=DowngradeStats)
    # Chunked-extraction facts (plan C1). Defaults keep single-shot callers valid.
    chunk_total: int = 1
    failed_chunks: int = 0
    coverage_ratio: float = 1.0
    validation_incomplete: bool = False
    prompt_hash: str = ""
    rendered_prompt_hash: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    key_references: list[dict] = Field(default_factory=list)
    # Parallel to ``fragments``: (char_start, char_end) into the page-marked
    # full text, or None when the span could not be located.
    spans: list[Optional[tuple[int, int]]] = Field(default_factory=list)
    verbatim_statuses: list[str] = Field(default_factory=list)
    source_locators: list[Optional[str]] = Field(default_factory=list)


class DailyPlan(BaseModel):
    """Generated daily briefing (Second Brain philosophy)."""

    date: str
    # Briefing fields
    focus: str = ""
    why: str = ""
    intervention: str = ""
    status_line: str = ""
    sources_needed: list[str] = Field(default_factory=list)
    strategy_suggestions: list[str] = Field(default_factory=list)
    briefing_text: str = ""
    # Legacy plan fields (used by CLI output and DB)
    dissertation_task: str = ""
    assistant_task: str = ""
    reading_target: str = ""
    reading_snippet: str = ""
    progress_summary: str = ""
    coverage_gaps: list[str] = Field(default_factory=list)


class CitationEntry(BaseModel):
    """Запланированная цитата для раздела."""

    citekey: str
    fragment_text: str = ""
    usage: str = ""  # evidence / method / comparison / definition / quote
    position: str = ""
    relevance: int = Field(3, ge=1, le=5)


class ArgumentBlock(BaseModel):
    """Логический блок структуры аргументации."""

    order: int
    title: str
    description: str
    citations: list[str] = Field(default_factory=list)
    estimated_words: int = 200


class ResearchResult(BaseModel):
    """Результат исследовательского анализа раздела."""

    section: str
    chapter: int
    section_title: str = ""
    section_status: str = ""
    current_word_count: int = 0
    target_word_count: int = 0
    readiness_pct: int = 0
    available_sources: int = 0
    available_fragments: int = 0
    fragment_distribution: dict[str, int] = Field(default_factory=dict)
    argument_blocks: list[ArgumentBlock] = Field(default_factory=list)
    citation_plan: list[CitationEntry] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)
    writing_suggestions: list[str] = Field(default_factory=list)
    filtered_citekeys: list[str] = Field(default_factory=list)
    required_missing: list[str] = Field(default_factory=list)
    research_text: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)


class LibraryReport(BaseModel):
    """AI-generated library analysis report."""

    mode: str = "status"  # status | recommend | audit
    overall_health: str = ""
    chapter_assessments: list[dict] = Field(default_factory=list)
    critical_issues: list[str] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    section_detail: dict = Field(default_factory=dict)
    audit_findings: list[dict] = Field(default_factory=list)
    prune: Optional[dict] = None  # {"drop": [{citekey, reason}], "maybe": [{citekey, reason}]}
    report_text: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)
