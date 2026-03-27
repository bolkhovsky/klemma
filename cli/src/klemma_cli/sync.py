"""Library sync — read local DB and push/pull via API."""

from __future__ import annotations

import base64
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .client import KlemmaClient
from .models import EmbeddingPayload, FragmentPayload, SourcePayload


def _find_library_db(project_root: Path) -> Path | None:
    """Find the library.db file. Checks project .klemma/data/ first, then ~/.klemma/."""
    candidates = [
        project_root / ".klemma" / "data" / "library.db",
        project_root / ".klemma" / "data" / "klemma.db",
        Path.home() / ".klemma" / "library.db",
        Path.home() / ".klemma" / "klemma.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _find_project_db(project_root: Path) -> Path | None:
    """Find the project.db file."""
    candidates = [
        project_root / ".klemma" / "data" / "project.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def read_local_sources(project_root: Path) -> list[SourcePayload]:
    """Read sources from local library.db."""
    db_path = _find_library_db(project_root)
    if not db_path:
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    sources = []
    try:
        # Read from user_sources + papers
        rows = conn.execute(
            """SELECT us.citekey, us.paper_id, us.status,
                      p.title, p.authors, p.year, p.doi, p.abstract
               FROM user_sources us
               LEFT JOIN papers p ON us.paper_id = p.paper_id
               ORDER BY us.added_at"""
        ).fetchall()

        for row in rows:
            # Get sections
            section_rows = conn.execute(
                "SELECT section FROM user_source_sections WHERE citekey = ?",
                (row["citekey"],),
            ).fetchall()
            sections = [r["section"] for r in section_rows]

            sources.append(SourcePayload(
                citekey=row["citekey"],
                paper_id=row["paper_id"] or "",
                title=row["title"] or "",
                authors=row["authors"] or "",
                year=row["year"],
                doi=row["doi"],
                abstract=row["abstract"] or "",
                sections=sections,
                status=row["status"] or "pending",
            ))
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return sources


def read_local_fragments(project_root: Path) -> list[FragmentPayload]:
    """Read fragments from local library.db."""
    db_path = _find_library_db(project_root)
    if not db_path:
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    fragments = []
    try:
        rows = conn.execute(
            """SELECT fragment_id, paper_id, fragment_text,
                      fragment_type, citation_intent, page_number
               FROM fragments ORDER BY rowid"""
        ).fetchall()

        for row in rows:
            fragments.append(FragmentPayload(
                fragment_id=row["fragment_id"],
                paper_id=row["paper_id"],
                text=row["fragment_text"],
                fragment_type=row["fragment_type"] or "key_idea",
                citation_intent=row["citation_intent"],
                page=row["page_number"],
            ))
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return fragments


def read_local_embeddings(
    project_root: Path,
) -> tuple[list[EmbeddingPayload], list[EmbeddingPayload]]:
    """Read embeddings from local library.db. Returns (paper_embs, fragment_embs)."""
    db_path = _find_library_db(project_root)
    if not db_path:
        return [], []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    paper_embs: list[EmbeddingPayload] = []
    fragment_embs: list[EmbeddingPayload] = []

    try:
        for row in conn.execute("SELECT paper_id, model_name, vector FROM paper_embeddings"):
            b64 = base64.b64encode(row["vector"]).decode()
            paper_embs.append(EmbeddingPayload(
                id=row["paper_id"], vector_b64=b64, model=row["model_name"],
            ))

        for row in conn.execute("SELECT fragment_id, model_name, vector FROM fragment_embeddings"):
            b64 = base64.b64encode(row["vector"]).decode()
            fragment_embs.append(EmbeddingPayload(
                id=row["fragment_id"], vector_b64=b64, model=row["model_name"],
            ))
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return paper_embs, fragment_embs


def push_library(client: KlemmaClient, project_root: Path) -> dict:
    """Push local library data to server. Returns summary dict."""
    sources = read_local_sources(project_root)
    fragments = read_local_fragments(project_root)

    result = {"sources": 0, "fragments": 0, "embeddings": 0}

    # Push in chunks to avoid timeout on large libraries
    src_chunk = 200
    frag_chunk = 1000
    src_list = [s.model_dump() for s in sources]
    frag_list = [f.model_dump() for f in fragments]

    # First chunk carries all sources + first batch of fragments
    for src_start in range(0, max(len(src_list), 1), src_chunk):
        src_batch = src_list[src_start:src_start + src_chunk]
        frag_start = (src_start // src_chunk) * frag_chunk
        frag_batch = frag_list[frag_start:frag_start + frag_chunk]
        if not src_batch and not frag_batch:
            break
        resp = client.post("/sync/push/library", json={
            "sources": src_batch,
            "fragments": frag_batch,
        })
        data = resp.json()
        result["sources"] += data.get("sources_saved", 0)
        result["fragments"] += data.get("fragments_saved", 0)

    # Push any remaining fragments not covered by source chunks
    src_batches_count = max(1, (len(src_list) + src_chunk - 1) // src_chunk)
    frag_offset = src_batches_count * frag_chunk
    for i in range(frag_offset, len(frag_list), frag_chunk):
        resp = client.post("/sync/push/library", json={
            "sources": [],
            "fragments": frag_list[i:i + frag_chunk],
        })
        data = resp.json()
        result["fragments"] += data.get("fragments_saved", 0)

    # Push embeddings in chunks
    paper_embs, fragment_embs = read_local_embeddings(project_root)
    chunk_size = 100
    for i in range(0, len(paper_embs), chunk_size):
        chunk_p = paper_embs[i:i + chunk_size]
        chunk_f = fragment_embs[i:i + chunk_size] if i < len(fragment_embs) else []
        client.post("/sync/push/embeddings", json={
            "paper_embeddings": [e.model_dump() for e in chunk_p],
            "fragment_embeddings": [e.model_dump() for e in chunk_f],
        })
        result["embeddings"] += len(chunk_p) + len(chunk_f)

    # Push remaining fragment embeddings
    remaining_start = len(paper_embs)
    if remaining_start < len(fragment_embs):
        for i in range(remaining_start, len(fragment_embs), chunk_size):
            chunk_f = fragment_embs[i:i + chunk_size]
            client.post("/sync/push/embeddings", json={
                "paper_embeddings": [],
                "fragment_embeddings": [e.model_dump() for e in chunk_f],
            })
            result["embeddings"] += len(chunk_f)

    return result


def push_drafts(client: KlemmaClient, project_root: Path, dashboard_project_id: str) -> dict:
    """Push local draft .md files to server's /projects/{id}/drafts API.

    Only pushes a file if the local copy is strictly newer than the server copy.
    This prevents older local files from overwriting dashboard edits.
    Returns summary dict with 'files' (pushed count), 'skipped', and 'words'.
    """
    drafts_dir = project_root / "draft"
    result: dict = {"files": 0, "skipped": 0, "words": 0}

    if not drafts_dir.exists():
        return result

    # Fetch server file timestamps once
    server_times: dict[str, datetime] = {}
    try:
        resp = client.get(f"/projects/{dashboard_project_id}/drafts")
        for f in resp.json().get("files", []):
            ts = f.get("updated_at", "")
            if ts:
                try:
                    server_times[f["name"]] = datetime.fromisoformat(ts)
                except ValueError:
                    pass
    except Exception:
        pass  # if list fails, push everything

    for md_file in sorted(drafts_dir.glob("*.md")):
        filename = md_file.name
        local_mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)
        server_mtime = server_times.get(filename)

        if server_mtime and server_mtime >= local_mtime:
            result["skipped"] += 1
            continue

        content = md_file.read_text(encoding="utf-8")
        resp = client.put(
            f"/projects/{dashboard_project_id}/drafts/{filename}",
            json={"content": content},
        )
        wc = resp.json().get("word_count", 0)
        result["files"] += 1
        result["words"] += wc

    return result


_SAFE_DRAFT_FILENAME = re.compile(r"^[\w.\-]+\.md$")


def pull_drafts(client: KlemmaClient, project_root: Path, dashboard_project_id: str) -> dict:
    """Pull draft .md files from server's /projects/{id}/drafts API to local draft/.

    Only writes a file if its content differs from the local copy (avoids noise).
    Skips files with unsafe names (path traversal guard).
    Returns summary dict with 'files' (updated count) and 'words' counts.
    """
    resp = client.get(f"/projects/{dashboard_project_id}/drafts")
    file_list = resp.json().get("files", [])
    result: dict = {"files": 0, "words": 0}

    if not file_list:
        return result

    draft_dir = project_root / "draft"
    draft_dir.mkdir(exist_ok=True)

    result["skipped"] = 0

    for file_info in file_list:
        name = file_info.get("name", "")
        if not _SAFE_DRAFT_FILENAME.match(name) or ".." in name:
            continue  # skip unsafe filenames

        target = draft_dir / name

        # Skip if local copy is strictly newer than server copy
        server_ts = file_info.get("updated_at", "")
        if server_ts and target.exists():
            try:
                server_mtime = datetime.fromisoformat(server_ts)
                local_mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
                if local_mtime > server_mtime:
                    result["skipped"] += 1
                    continue
            except ValueError:
                pass

        content_resp = client.get(f"/projects/{dashboard_project_id}/drafts/{name}")
        data = content_resp.json()
        content: str = data.get("content", "")
        wc: int = data.get("word_count", 0)

        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue  # already up to date (same content)

        target.write_text(content, encoding="utf-8")
        result["files"] += 1
        result["words"] += wc

    return result


def pull_library(client: KlemmaClient, project_root: Path, since: Optional[str] = None) -> dict:
    """Pull library data from server and write to local DB. Returns summary dict."""
    params = {}
    if since:
        params["since"] = since

    resp = client.get("/sync/pull/library", params=params)
    data = resp.json()

    sources = data.get("sources", [])
    fragments = data.get("fragments", [])

    result = {"sources": 0, "fragments": 0}

    # Always write to the new-format library.db (never to legacy klemma.db)
    db_path = project_root / ".klemma" / "data" / "library.db"
    if not db_path.exists():
        # Fallback to system-level library.db
        system_db = Path.home() / ".klemma" / "library.db"
        if system_db.exists():
            db_path = system_db
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        # Ensure tables exist (simplified schema — no datetime defaults)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS papers ("
            "paper_id TEXT PRIMARY KEY, pdf_hash TEXT, doi TEXT, s2_paper_id TEXT,"
            "title TEXT NOT NULL DEFAULT '', authors TEXT, year INTEGER, abstract TEXT,"
            "created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_sources ("
            "citekey TEXT PRIMARY KEY, paper_id TEXT NOT NULL,"
            "status TEXT DEFAULT 'pending', pdf_path TEXT, note_path TEXT,"
            "quality_score INTEGER, added_at TEXT, updated_at TEXT,"
            "project_id TEXT, user_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_source_sections ("
            "citekey TEXT NOT NULL, section TEXT NOT NULL,"
            "PRIMARY KEY (citekey, section))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fragments ("
            "fragment_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,"
            "extraction_id TEXT, fragment_text TEXT NOT NULL,"
            "fragment_type TEXT, page_number INTEGER, citation_intent TEXT,"
            "created_at TEXT)"
        )

        for src in sources:
            conn.execute(
                """INSERT OR REPLACE INTO papers
                   (paper_id, title, authors, year, doi, abstract)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (src["paper_id"], src.get("title", ""), src.get("authors", ""),
                 src.get("year"), src.get("doi"), src.get("abstract", "")),
            )
            conn.execute(
                """INSERT INTO user_sources (citekey, paper_id, status)
                   VALUES (?, ?, ?)
                   ON CONFLICT(citekey) DO UPDATE SET
                       paper_id=excluded.paper_id, status=excluded.status,
                       updated_at=datetime('now')""",
                (src["citekey"], src["paper_id"], src.get("status", "pending")),
            )
            # Update sections
            conn.execute(
                "DELETE FROM user_source_sections WHERE citekey = ?",
                (src["citekey"],),
            )
            for section in src.get("sections", []):
                conn.execute(
                    "INSERT OR IGNORE INTO user_source_sections (citekey, section) VALUES (?, ?)",
                    (src["citekey"], section),
                )
            result["sources"] += 1

        for frag in fragments:
            conn.execute(
                """INSERT OR IGNORE INTO fragments
                   (fragment_id, paper_id, fragment_text, fragment_type,
                    page_number, citation_intent)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (frag["fragment_id"], frag["paper_id"], frag["text"],
                 frag.get("fragment_type", "key_idea"),
                 frag.get("page"), frag.get("citation_intent")),
            )
            result["fragments"] += 1

        conn.commit()
    finally:
        conn.close()

    return result
