"""Citation links and graph analysis repository."""

import hashlib
from typing import Optional

from .base import BaseRepository


class CitationsRepository(BaseRepository):

    def save_citation_links(self, source_id: str, references: list[dict]):
        """Save citation links from annotation key_references.

        Each reference dict should have: authors, year, title, citation_intent,
        in_library, citekey (optional).
        Uses MD5 of normalized title for UNIQUE constraint.
        """
        with self._conn() as conn:
            for ref in references:
                title = ref.get("title", "")
                if not title:
                    continue
                title_hash = hashlib.md5(
                    title.lower().strip().encode()
                ).hexdigest()
                conn.execute(
                    """INSERT OR REPLACE INTO citation_links
                       (source_id, target_citekey, target_title_hash,
                        target_title, target_authors, target_year,
                        citation_intent, in_library)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        ref.get("citekey"),
                        title_hash,
                        title,
                        ref.get("authors", ""),
                        ref.get("year"),
                        ref.get("citation_intent"),
                        1 if ref.get("in_library") else 0,
                    ),
                )

    def get_citation_links(
        self, source_id: Optional[str] = None
    ) -> list[dict]:
        """Get citation links, optionally filtered by source."""
        with self._conn() as conn:
            if source_id:
                cur = conn.execute(
                    "SELECT * FROM citation_links WHERE source_id=?",
                    (source_id,),
                )
            else:
                cur = conn.execute("SELECT * FROM citation_links")
            return [dict(row) for row in cur.fetchall()]

    def get_citation_graph_stats(self) -> dict:
        """Compute citation graph statistics."""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM citation_links"
            ).fetchone()["cnt"]
            unique_targets = conn.execute(
                "SELECT COUNT(DISTINCT target_title_hash) as cnt FROM citation_links"
            ).fetchone()["cnt"]
            in_library = conn.execute(
                "SELECT COUNT(*) as cnt FROM citation_links WHERE in_library=1"
            ).fetchone()["cnt"]
            external = total - in_library

            source_count = conn.execute(
                "SELECT COUNT(DISTINCT source_id) as cnt FROM citation_links"
            ).fetchone()["cnt"]
            avg_refs = round(total / source_count, 1) if source_count else 0

            # Most cited external works
            cur = conn.execute(
                """SELECT target_title, target_authors, target_year,
                          COUNT(DISTINCT source_id) as cite_count
                   FROM citation_links
                   WHERE in_library=0
                   GROUP BY target_title_hash
                   ORDER BY cite_count DESC
                   LIMIT 10"""
            )
            most_cited_external = [dict(row) for row in cur.fetchall()]

            # Most connected internal works
            cur = conn.execute(
                """SELECT target_citekey, COUNT(DISTINCT source_id) as cite_count
                   FROM citation_links
                   WHERE in_library=1 AND target_citekey IS NOT NULL
                   GROUP BY target_citekey
                   ORDER BY cite_count DESC
                   LIMIT 10"""
            )
            most_connected = [dict(row) for row in cur.fetchall()]

            return {
                "total_links": total,
                "unique_targets": unique_targets,
                "in_library": in_library,
                "external": external,
                "source_count": source_count,
                "avg_refs_per_source": avg_refs,
                "most_cited_external": most_cited_external,
                "most_connected_internal": most_connected,
            }

    def get_co_cited(self, citekey: str) -> list[dict]:
        """Find works frequently co-cited with the given citekey."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT DISTINCT source_id FROM citation_links
                   WHERE target_citekey=? OR target_title_hash IN (
                       SELECT target_title_hash FROM citation_links
                       WHERE target_citekey=?
                   )""",
                (citekey, citekey),
            )
            citing_sources = [row["source_id"] for row in cur.fetchall()]

            if not citing_sources:
                return []

            placeholders = ",".join("?" * len(citing_sources))
            cur = conn.execute(
                f"""SELECT target_title, target_authors, target_year,
                           target_citekey, in_library,
                           COUNT(DISTINCT source_id) as co_cite_count
                    FROM citation_links
                    WHERE source_id IN ({placeholders})
                      AND (target_citekey IS NULL OR target_citekey != ?)
                    GROUP BY target_title_hash
                    ORDER BY co_cite_count DESC
                    LIMIT 20""",
                (*citing_sources, citekey),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_key_author_groups(self, min_papers: int = 2) -> list[dict]:
        """Find author groups with multiple papers in the library."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT target_authors, target_title, target_year, target_citekey, in_library
                   FROM citation_links
                   WHERE target_authors IS NOT NULL AND target_authors != ''"""
            )

            groups: dict[str, list[dict]] = {}
            for row in cur.fetchall():
                authors = row["target_authors"]
                surname = ""
                for word in authors.replace("et al.", "").replace(",", " ").split():
                    clean = word.strip(".").strip()
                    if len(clean) > 2 and clean[0].isupper():
                        surname = clean
                        break
                if not surname:
                    continue
                if surname not in groups:
                    groups[surname] = []
                groups[surname].append({
                    "title": row["target_title"],
                    "year": row["target_year"],
                    "citekey": row["target_citekey"],
                    "in_library": bool(row["in_library"]),
                    "full_authors": authors,
                })

            result = []
            for surname, papers in groups.items():
                unique_titles = {p["title"] for p in papers}
                if len(unique_titles) >= min_papers:
                    result.append({
                        "surname": surname,
                        "paper_count": len(unique_titles),
                        "in_library_count": sum(1 for p in papers if p["in_library"]),
                        "papers": papers[:10],
                    })
            result.sort(key=lambda x: x["paper_count"], reverse=True)
            return result
