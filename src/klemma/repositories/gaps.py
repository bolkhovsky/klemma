"""Reference gaps, coverage, and scoring repository."""

import json
from typing import Optional

from .base import BaseRepository


class GapsRepository(BaseRepository):

    def save_reference_gaps(self, source_id: str, gaps: list[dict]) -> int:
        """Save reference gaps for a source (idempotent: replaces old gaps)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM reference_gaps WHERE source_id=?", (source_id,)
            )
            for g in gaps:
                sections = g.get("dissertation_sections", [])
                conn.execute(
                    """INSERT INTO reference_gaps
                       (source_id, ref_authors, ref_year, ref_title,
                        why_relevant, dissertation_sections, citation_intent)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        g.get("ref_authors", g.get("authors", "")),
                        g.get("ref_year", g.get("year")),
                        g.get("ref_title", g.get("title", "")),
                        g.get("why_relevant", ""),
                        json.dumps(sections) if sections else None,
                        g.get("citation_intent"),
                    ),
                )
            return len(gaps)

    def get_reference_gaps(
        self,
        section: Optional[str] = None,
        limit: int = 50,
        section_weights: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """Get open reference gaps aggregated by (authors, year, title).

        Scoring formula: count * avg_quality * section_weight * intent_weight
        where intent_weight = AVG(method=3.0, result_comparison=2.0, else=1.0)
        NULL intents -> weight 1.0 (backward compatible).
        """
        with self._conn() as conn:
            query = """
                SELECT
                    rg.ref_authors,
                    rg.ref_year,
                    rg.ref_title,
                    GROUP_CONCAT(DISTINCT rg.why_relevant) as why_relevant,
                    GROUP_CONCAT(DISTINCT rg.dissertation_sections) as dissertation_sections,
                    COUNT(DISTINCT rg.source_id) as count,
                    AVG(COALESCE(s.quality_score, 3)) as avg_quality,
                    AVG(CASE
                        WHEN rg.citation_intent = 'method' THEN 3.0
                        WHEN rg.citation_intent = 'result_comparison' THEN 2.0
                        ELSE 1.0
                    END) as intent_weight,
                    GROUP_CONCAT(DISTINCT rg.source_id) as source_ids
                FROM reference_gaps rg
                JOIN sources s ON rg.source_id = s.id
                WHERE rg.status = 'open'
            """
            params: list = []
            if section:
                query += " AND rg.dissertation_sections LIKE ?"
                params.append(f'%"{section}%')

            query += """
                GROUP BY rg.ref_authors, rg.ref_year, rg.ref_title
                ORDER BY COUNT(DISTINCT rg.source_id)
                       * AVG(COALESCE(s.quality_score, 3))
                       * AVG(CASE
                           WHEN rg.citation_intent = 'method' THEN 3.0
                           WHEN rg.citation_intent = 'result_comparison' THEN 2.0
                           ELSE 1.0
                         END)
                       DESC
                LIMIT ?
            """
            params.append(limit)

            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                r = dict(row)
                count = r["count"]
                avg_q = r["avg_quality"] or 3
                intent_w = r.get("intent_weight") or 1.0
                sections_str = r.get("dissertation_sections") or ""
                section_weight = self._compute_section_weight(
                    sections_str, section_weights
                )
                r["score"] = round(count * avg_q * section_weight * intent_w, 1)
                results.append(r)
            results.sort(key=lambda g: g["score"], reverse=True)
            return results

    @staticmethod
    def _compute_section_weight(
        sections_str: str,
        section_weights: Optional[dict[str, float]],
    ) -> float:
        """Compute max section weight from JSON-serialized sections list.

        When section_weights is None (no config), all sections get 1.0 (uniform).
        When section_weights is provided, unlisted sections default to 0.5.
        """
        if section_weights is None:
            return 1.0
        if not sections_str:
            return 0.5
        try:
            # sections_str may contain multiple GROUP_CONCAT'd JSON arrays
            # e.g. '["2.1","2.3"],["2.1"]' — split and parse each
            sections: list[str] = []
            for part in sections_str.split("],"):
                part = part.strip().rstrip(",")
                if not part.endswith("]"):
                    part += "]"
                try:
                    parsed = json.loads(part)
                    if isinstance(parsed, list):
                        sections.extend(str(s) for s in parsed)
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            return 0.5
        if not sections:
            return 0.5
        return max(section_weights.get(s, 0.5) for s in sections)

    def rerank_gaps_semantic(
        self,
        gaps: list[dict],
        embeddings=None,
        query_section: Optional[str] = None,
        get_all_embeddings=None,
        get_section_sources=None,
    ) -> list[dict]:
        """Rerank reference gaps using embedding similarity to section centroid.

        Formula: final_score = heuristic_score * (0.5 + 0.5 * sim_to_centroid)
        No embeddings -> returns gaps unchanged.

        get_all_embeddings and get_section_sources are callables injected by
        StateManager to avoid cross-repo coupling.
        """
        if not embeddings or not get_all_embeddings:
            return gaps

        from ..embeddings import cosine_similarity

        all_emb = get_all_embeddings(model=embeddings.model_name)
        if not all_emb:
            return gaps

        # Compute section centroid from sources assigned to that section
        centroid = None
        if query_section and get_section_sources:
            section_sources = get_section_sources(query_section)
            section_vecs = [
                all_emb[sid] for sid in section_sources if sid in all_emb
            ]
            if section_vecs:
                dim = len(section_vecs[0])
                centroid = [
                    sum(v[i] for v in section_vecs) / len(section_vecs)
                    for i in range(dim)
                ]

        # If no section centroid, use global centroid of all embeddings
        if not centroid and all_emb:
            vecs = list(all_emb.values())
            dim = len(vecs[0])
            centroid = [
                sum(v[i] for v in vecs) / len(vecs) for i in range(dim)
            ]

        if not centroid:
            return gaps

        # Rerank: boost by average similarity of citing sources to centroid
        for gap in gaps:
            source_ids = (gap.get("source_ids") or "").split(",")
            sims = []
            for sid in source_ids:
                sid = sid.strip()
                if sid in all_emb:
                    sims.append(cosine_similarity(all_emb[sid], centroid))
            if sims:
                avg_sim = sum(sims) / len(sims)
                gap["semantic_boost"] = round(avg_sim, 4)
                gap["score"] = round(gap["score"] * (0.5 + 0.5 * avg_sim), 1)
            else:
                gap["semantic_boost"] = 0.0

        gaps.sort(key=lambda g: g["score"], reverse=True)
        return gaps

    def get_section_sources(self, section: str) -> list[str]:
        """Get source IDs assigned to a section (via source_sections or primary_section)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT source_id FROM source_sections WHERE section LIKE ?",
                (f"{section}%",),
            )
            ids = [row["source_id"] for row in cur.fetchall()]
            if not ids:
                cur = conn.execute(
                    "SELECT id FROM sources WHERE primary_section LIKE ?",
                    (f"{section}%",),
                )
                ids = [row["id"] for row in cur.fetchall()]
            return ids

    def get_gap_summary(self) -> dict:
        """Lightweight summary for status line: open count + top gap."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(DISTINCT ref_authors || ref_year || ref_title) as cnt "
                "FROM reference_gaps WHERE status='open'"
            )
            open_count = cur.fetchone()["cnt"]

            top_ref = None
            top_count = 0
            if open_count > 0:
                cur = conn.execute(
                    """SELECT ref_authors, ref_year,
                              COUNT(DISTINCT source_id) as cnt
                       FROM reference_gaps WHERE status='open'
                       GROUP BY ref_authors, ref_year, ref_title
                       ORDER BY cnt DESC LIMIT 1"""
                )
                row = cur.fetchone()
                if row:
                    year_str = str(row["ref_year"]) if row["ref_year"] else ""
                    top_ref = f"{row['ref_authors']} {year_str}".strip()
                    top_count = row["cnt"]

            return {
                "open_count": open_count,
                "top_ref": top_ref,
                "top_count": top_count,
            }

    def resolve_gaps(self, entry_lookup: dict) -> int:
        """Auto-resolve open gaps that match entries in library.

        Matches by (author surname, year) or title prefix.
        Returns count of resolved gaps.
        """
        lib_index: dict[str, str] = {}
        for citekey, entry in entry_lookup.items():
            if entry.author:
                family = entry.author[0].family.lower().strip()
                surname = family.split()[-1] if family else ""
            else:
                surname = ""
            year = str(entry.year) if entry.year else ""
            if surname and year:
                lib_index[f"{surname} {year}"] = citekey
            if entry.title:
                lib_index[entry.title.lower()[:50]] = citekey

        resolved = 0
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, ref_authors, ref_year, ref_title "
                "FROM reference_gaps WHERE status='open'"
            )
            for row in cur.fetchall():
                authors = (row["ref_authors"] or "").lower()
                year = str(row["ref_year"]) if row["ref_year"] else ""
                words = authors.replace("et al.", "").replace("\u0438 \u0434\u0440.", "").split()
                surname = ""
                for w in words:
                    clean = w.strip(".,")
                    if len(clean) > 2:
                        surname = clean
                        break
                key = f"{surname} {year}"

                matched_citekey = lib_index.get(key)
                if not matched_citekey and row["ref_title"]:
                    matched_citekey = lib_index.get(row["ref_title"].lower()[:50])

                if matched_citekey:
                    conn.execute(
                        """UPDATE reference_gaps
                           SET status='resolved', resolved_citekey=?,
                               resolved_at=datetime('now')
                           WHERE id=?""",
                        (matched_citekey, row["id"]),
                    )
                    resolved += 1
        return resolved

    # ── Coverage ──────────────────────────────────────────────────────────

    def get_coverage_stats(self) -> dict:
        with self._conn() as conn:
            stats: dict = {"chapters": {}, "sections": {}, "nr1": {}, "nr2": {}}
            cur = conn.execute(
                """SELECT primary_chapter, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_chapter IS NOT NULL
                   GROUP BY primary_chapter""",
                ("completed",),
            )
            for row in cur.fetchall():
                stats["chapters"][row["primary_chapter"]] = row["cnt"]

            cur = conn.execute(
                """SELECT primary_section, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_section IS NOT NULL
                   GROUP BY primary_section""",
                ("completed",),
            )
            for row in cur.fetchall():
                stats["sections"][row["primary_section"]] = row["cnt"]

            for level in range(1, 6):
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=? AND relevance_nr1>=?",
                    ("completed", level),
                )
                stats["nr1"][f">={level}"] = cur.fetchone()["cnt"]
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=? AND relevance_nr2>=?",
                    ("completed", level),
                )
                stats["nr2"][f">={level}"] = cur.fetchone()["cnt"]
            return stats

    def get_gaps(self, min_sources: int = 3) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT primary_section, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_section IS NOT NULL
                   GROUP BY primary_section HAVING cnt < ?
                   ORDER BY cnt ASC""",
                ("completed", min_sources),
            )
            return [
                {"section": row["primary_section"], "count": row["cnt"]}
                for row in cur.fetchall()
            ]

    def reset_non_completed(self) -> dict[str, int]:
        with self._conn() as conn:
            counts = {}
            for s in ["failed", "skipped", "processing"]:
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=?", (s,)
                )
                counts[s] = cur.fetchone()["cnt"]
            conn.execute(
                "UPDATE sources SET status=?, error_message=NULL WHERE status IN (?,?,?)",
                ("pending", "failed", "skipped", "processing"),
            )
            return counts
