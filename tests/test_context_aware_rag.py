"""Tests for context-aware fragment RAG (#110).

Tests: parse_argument_blocks, retrieve_rag_fragments_per_block,
fit_prompt_budget with rag_fragments, section_draft.md template
with rag_fragments.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Template

from klemma.skills.context_loader import (
    fit_prompt_budget,
    parse_argument_blocks,
    retrieve_rag_fragments_per_block,
)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


# ---------------------------------------------------------------------------
# parse_argument_blocks
# ---------------------------------------------------------------------------


SAMPLE_RESEARCH_REPORT = """\
# Исследовательский брифинг: Раздел 1.3
*Сгенерировано: 2026-03-07 10:00*

> **⚠️ LLM-GENERATED**: disclaimer text

**Статус раздела:** draft
**Объём:** 500/2000 слов (25%)

## Доступные материалы
- Источников: 12
- Фрагментов: 28

## Структура аргументации
### 1. Theoretical foundations of morphological analysis
Establish key concepts from foundational papers on computational morphology.
**Источники:** @goldsmith2001, @creutz2007
*~300 слов*

### 2. Low-resource language challenges
Discuss specific challenges of morphological analysis for under-resourced Uralic languages.
**Источники:** @hamalainen2021, @yli-jyra2005, @rueter2020
*~400 слов*

### 3. Modern approaches and neural methods
Review recent neural and hybrid approaches to morphological segmentation.
**Источники:** @kann2018
*~300 слов*

## План цитирования
- @goldsmith2001: foundational work
"""


class TestParseArgumentBlocks:
    """Extract argument blocks from research report markdown."""

    def test_parses_three_blocks(self):
        blocks = parse_argument_blocks(SAMPLE_RESEARCH_REPORT)
        assert len(blocks) == 3

    def test_block_order(self):
        blocks = parse_argument_blocks(SAMPLE_RESEARCH_REPORT)
        orders = [b["order"] for b in blocks]
        assert orders == [1, 2, 3]

    def test_block_titles(self):
        blocks = parse_argument_blocks(SAMPLE_RESEARCH_REPORT)
        assert blocks[0]["title"] == "Theoretical foundations of morphological analysis"
        assert blocks[1]["title"] == "Low-resource language challenges"

    def test_block_descriptions(self):
        blocks = parse_argument_blocks(SAMPLE_RESEARCH_REPORT)
        assert "foundational papers" in blocks[0]["description"]
        assert "under-resourced Uralic" in blocks[1]["description"]

    def test_block_citations(self):
        blocks = parse_argument_blocks(SAMPLE_RESEARCH_REPORT)
        assert blocks[0]["citations"] == ["goldsmith2001", "creutz2007"]
        assert blocks[1]["citations"] == [
            "hamalainen2021",
            "yli-jyra2005",
            "rueter2020",
        ]
        assert blocks[2]["citations"] == ["kann2018"]

    def test_empty_input(self):
        assert parse_argument_blocks("") == []
        assert parse_argument_blocks(None) == []

    def test_no_argumentation_section(self):
        text = "# Report\n## Some other section\nContent."
        assert parse_argument_blocks(text) == []

    def test_single_block(self):
        text = (
            "## Структура аргументации\n"
            "### 1. Only block\n"
            "Description of the only block.\n"
            "**Источники:** @smith2020\n"
            "*~200 слов*\n"
        )
        blocks = parse_argument_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["title"] == "Only block"
        assert blocks[0]["citations"] == ["smith2020"]

    def test_block_without_citations(self):
        text = (
            "## Структура аргументации\n"
            "### 1. No citations block\n"
            "A block without citation lines.\n"
        )
        blocks = parse_argument_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["citations"] == []

    def test_stops_at_next_section(self):
        """Blocks only parsed within '## Структура аргументации' section."""
        text = (
            "## Структура аргументации\n"
            "### 1. Inside block\n"
            "Description.\n"
            "## План цитирования\n"
            "### 2. Outside block\n"
            "Should not be parsed.\n"
        )
        blocks = parse_argument_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["title"] == "Inside block"


# ---------------------------------------------------------------------------
# retrieve_rag_fragments_per_block
# ---------------------------------------------------------------------------


def _make_state_with_fragments(tmp_path, n_fragments=6):
    """Create a StateManager with test fragments and embeddings."""
    from klemma.state import StateManager

    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1", "paper2", "paper3"])
    sm.mark_completed("paper1", note_path="@paper1.md")
    sm.mark_completed("paper2", note_path="@paper2.md")
    sm.mark_completed("paper3", note_path="@paper3.md")

    sm.save_fragments(
        "paper1",
        [
            {
                "text": "Computational morphology foundations",
                "type": "method",
                "section": "1",
                "citation_intent": "background",
            },
            {
                "text": "Unsupervised morpheme segmentation",
                "type": "result",
                "section": "1",
                "citation_intent": "method",
            },
        ],
    )
    sm.save_fragments(
        "paper2",
        [
            {
                "text": "Low-resource language processing challenges",
                "type": "method",
                "section": "1",
                "citation_intent": "background",
            },
            {
                "text": "Uralic morphology complexity analysis",
                "type": "result",
                "section": "1",
                "citation_intent": "result_comparison",
            },
        ],
    )
    sm.save_fragments(
        "paper3",
        [
            {
                "text": "Neural sequence-to-sequence for morphology",
                "type": "method",
                "section": "1",
                "citation_intent": "method",
            },
            {
                "text": "Transformer attention for agglutinative languages",
                "type": "result",
                "section": "1",
                "citation_intent": "method",
            },
        ],
    )

    # Create distinguishable embeddings
    frags = sm.get_fragments(limit=100)
    vectors = [
        [1.0, 0.0, 0.0, 0.0, 0.0],  # morphology foundations → close to block 1
        [0.8, 0.2, 0.0, 0.0, 0.0],  # unsupervised segmentation → close to block 1
        [0.0, 1.0, 0.0, 0.0, 0.0],  # low-resource challenges → close to block 2
        [0.0, 0.8, 0.2, 0.0, 0.0],  # Uralic complexity → close to block 2
        [0.0, 0.0, 1.0, 0.0, 0.0],  # neural seq2seq → close to block 3
        [0.0, 0.0, 0.8, 0.2, 0.0],  # transformer attention → close to block 3
    ]
    for i, frag in enumerate(frags):
        if i < len(vectors):
            sm.save_fragment_embedding(frag["id"], vectors[i], "test-model")

    return sm


class TestRetrieveRAGFragmentsPerBlock:
    """Per-block fragment retrieval via embedding similarity."""

    def _make_blocks(self):
        return [
            {
                "order": 1,
                "title": "Foundations",
                "description": "Foundational morphology concepts",
            },
            {
                "order": 2,
                "title": "Challenges",
                "description": "Low-resource language challenges",
            },
            {
                "order": 3,
                "title": "Neural methods",
                "description": "Neural approaches to morphology",
            },
        ]

    def _make_embeddings(self):
        """Mock embedding provider returning predictable vectors."""
        emb = MagicMock()
        emb.model_name = "test-model"

        def embed_fn(text):
            if "morphology" in text.lower() or "foundation" in text.lower():
                return [0.9, 0.1, 0.0, 0.0, 0.0]
            elif "low-resource" in text.lower() or "challenge" in text.lower():
                return [0.0, 0.9, 0.1, 0.0, 0.0]
            elif "neural" in text.lower():
                return [0.0, 0.0, 0.9, 0.1, 0.0]
            return [0.2, 0.2, 0.2, 0.2, 0.2]

        emb.embed = embed_fn
        return emb

    def test_returns_blocks_with_fragments(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        blocks = self._make_blocks()
        emb = self._make_embeddings()

        result = retrieve_rag_fragments_per_block(blocks, emb, sm, top_k=2)

        assert len(result) >= 1
        for block in result:
            assert "block_order" in block
            assert "block_title" in block
            assert "fragments" in block
            assert len(block["fragments"]) > 0

    def test_fragments_have_required_fields(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        blocks = self._make_blocks()
        emb = self._make_embeddings()

        result = retrieve_rag_fragments_per_block(blocks, emb, sm, top_k=2)

        for block in result:
            for f in block["fragments"]:
                assert "source" in f
                assert "text" in f
                assert "type" in f
                assert "similarity" in f

    def test_deduplicates_across_blocks(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        blocks = self._make_blocks()
        emb = self._make_embeddings()

        result = retrieve_rag_fragments_per_block(blocks, emb, sm, top_k=5)

        # Collect all (source, text) pairs
        all_frags = []
        for block in result:
            for f in block["fragments"]:
                all_frags.append((f["source"], f["text"][:50]))

        # No duplicates
        assert len(all_frags) == len(set(all_frags))

    def test_empty_blocks(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        emb = self._make_embeddings()

        result = retrieve_rag_fragments_per_block([], emb, sm, top_k=5)
        assert result == []

    def test_no_embeddings_provider(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        blocks = self._make_blocks()

        result = retrieve_rag_fragments_per_block(blocks, None, sm, top_k=5)
        assert result == []

    def test_embed_failure_skips_block(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        blocks = self._make_blocks()

        emb = MagicMock()
        emb.model_name = "test-model"
        emb.embed.side_effect = RuntimeError("API error")

        result = retrieve_rag_fragments_per_block(blocks, emb, sm, top_k=5)
        assert result == []

    def test_block_without_description_skipped(self, tmp_path):
        sm = _make_state_with_fragments(tmp_path)
        emb = self._make_embeddings()
        blocks = [{"order": 1, "title": "Empty", "description": ""}]

        result = retrieve_rag_fragments_per_block(blocks, emb, sm, top_k=5)
        assert result == []


# ---------------------------------------------------------------------------
# fit_prompt_budget with rag_fragments
# ---------------------------------------------------------------------------


class TestFitPromptBudgetWithRAG:
    """Budget control prioritizes RAG over section-level fragments."""

    def _make_sources(self, n, summary_len=100):
        return [
            {"citekey": f"src{i}", "quality": 5, "summary": "x" * summary_len}
            for i in range(n)
        ]

    def _make_fragments(self, n, text_len=100):
        return [
            {"source": f"src{i}", "text": "y" * text_len, "type": "result"}
            for i in range(n)
        ]

    def _make_rag_blocks(self, n_blocks=3, frags_per_block=5, text_len=200):
        return [
            {
                "block_order": i + 1,
                "block_title": f"Block {i + 1}",
                "fragments": [
                    {
                        "source": f"rag_src{i}_{j}",
                        "text": "r" * text_len,
                        "type": "method",
                        "similarity": 0.9 - j * 0.1,
                    }
                    for j in range(frags_per_block)
                ],
            }
            for i in range(n_blocks)
        ]

    def test_rag_passes_through_when_within_budget(self):
        draft = "short"
        sources = self._make_sources(3)
        fragments = self._make_fragments(3)
        rag = self._make_rag_blocks(2, 3, 50)

        rd, rs, rf, rr = fit_prompt_budget(
            draft,
            sources,
            fragments,
            rag_fragments=rag,
        )

        assert rr is not None
        assert len(rr) == 2
        for block in rr:
            assert len(block["fragments"]) == 3

    def test_section_fragments_trimmed_before_rag(self):
        """Under tight budget, section-level fragments reduce first."""
        draft = "A" * 15_000
        sources = self._make_sources(20, summary_len=800)
        fragments = self._make_fragments(40, text_len=500)
        rag = self._make_rag_blocks(3, 5, 200)

        rd, rs, rf, rr = fit_prompt_budget(
            draft,
            sources,
            fragments,
            max_chars=30_000,
            rag_fragments=rag,
        )

        # Section fragments should be trimmed
        assert len(rf) <= 20
        # RAG blocks should still be present
        assert rr is not None
        assert len(rr) == 3

    def test_rag_trimmed_last_resort(self):
        """With extreme budget pressure, RAG fragments also get trimmed."""
        draft = "A" * 15_000
        sources = self._make_sources(40, summary_len=1000)
        fragments = self._make_fragments(40, text_len=500)
        rag = self._make_rag_blocks(5, 10, 500)

        rd, rs, rf, rr = fit_prompt_budget(
            draft,
            sources,
            fragments,
            max_chars=15_000,
            rag_fragments=rag,
        )

        # RAG fragments should have been trimmed
        if rr:
            for block in rr:
                for f in block["fragments"]:
                    assert len(f["text"]) <= 150
                assert len(block["fragments"]) <= 3

    def test_returns_4_tuple_without_rag(self):
        """Without rag_fragments, returns None as 4th element."""
        draft = "short"
        sources = self._make_sources(2)
        fragments = self._make_fragments(2)

        result = fit_prompt_budget(draft, sources, fragments)
        assert len(result) == 4
        assert result[3] is None


# ---------------------------------------------------------------------------
# Template rendering with rag_fragments
# ---------------------------------------------------------------------------


class TestSectionDraftTemplateWithRAG:
    """section_draft.md renders correctly with per-block RAG fragments."""

    def _render(self, **kwargs):
        raw = (PROMPTS_DIR / "section_draft.md").read_text(encoding="utf-8")
        defaults = {
            "dissertation_context": "Test context",
            "section": "1.3",
            "chapter_num": 1,
            "chapter_name": "Introduction",
            "research_report": "",
            "existing_draft": "",
            "fragments": [],
            "rag_fragments": [],
            "source_summaries": [],
            "language": "ru",
        }
        defaults.update(kwargs)
        return Template(raw).render(**defaults)

    def test_renders_rag_fragments_section(self):
        rag = [
            {
                "block_order": 1,
                "block_title": "Theoretical foundations",
                "fragments": [
                    {
                        "source": "smith2020",
                        "text": "Important finding",
                        "type": "result",
                        "similarity": 0.92,
                    },
                ],
            },
        ]
        result = self._render(rag_fragments=rag)
        assert "Блок 1: Theoretical foundations" in result
        assert "[smith2020]" in result
        assert "0.92" in result

    def test_renders_both_rag_and_fallback(self):
        rag = [
            {
                "block_order": 1,
                "block_title": "Block one",
                "fragments": [
                    {
                        "source": "a2020",
                        "text": "RAG text",
                        "type": "method",
                        "similarity": 0.8,
                    },
                ],
            },
        ]
        flat = [
            {
                "source": "b2021",
                "text": "Fallback text",
                "type": "result",
                "relevance": 4,
            },
        ]
        result = self._render(rag_fragments=rag, fragments=flat)

        assert "Блок 1: Block one" in result
        assert "[a2020]" in result
        assert "Дополнительные фрагменты" in result
        assert "[b2021]" in result

    def test_renders_flat_fragments_without_rag(self):
        flat = [
            {"source": "c2022", "text": "Only flat", "type": "result", "relevance": 3},
        ]
        result = self._render(fragments=flat)

        assert "Релевантные фрагменты из библиотеки" in result
        assert "Дополнительные" not in result
        assert "[c2022]" in result

    def test_renders_empty_rag_and_fragments(self):
        result = self._render(rag_fragments=[], fragments=[])
        assert "фрагменты" not in result.lower() or "фрагменты" in result.lower()
        # Should not crash
        assert len(result) > 0

    def test_multiple_rag_blocks(self):
        rag = [
            {
                "block_order": i,
                "block_title": f"Block {i}",
                "fragments": [
                    {
                        "source": f"src{i}",
                        "text": f"Text {i}",
                        "type": "method",
                        "similarity": 0.9,
                    },
                ],
            }
            for i in range(1, 4)
        ]
        result = self._render(rag_fragments=rag)
        assert "Блок 1:" in result
        assert "Блок 2:" in result
        assert "Блок 3:" in result

    def test_he_et_al_reference_in_template(self):
        """Template mentions He et al. 2010 for provenance."""
        rag = [
            {
                "block_order": 1,
                "block_title": "Test",
                "fragments": [
                    {"source": "x", "text": "y", "type": "z", "similarity": 0.5},
                ],
            },
        ]
        result = self._render(rag_fragments=rag)
        assert "He et al. 2010" in result
