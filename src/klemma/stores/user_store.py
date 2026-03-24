"""LocalUserStore — SQLite implementation of the UserStore protocol (ADR-009).

Stores user accounts and refresh tokens for the SaaS auth layer.
Separate DB file (users.db) from the library data.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from ..models import UserRecord

_SCHEMA_VERSION = 7

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    name            TEXT DEFAULT '',
    email_verified  INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);
"""


def _username_from_email(email: str) -> str:
    """Generate a username slug from email prefix.

    "ilya.bolkhovsky@gmail.com" → "ilya-bolkhovsky"
    "test@example.com" → "test"
    "Dr.Smith+work@university.edu" → "dr-smith"
    """
    prefix = email.split("@")[0]
    # Remove + aliases (gmail style)
    prefix = prefix.split("+")[0]
    # Replace dots, underscores with hyphens
    slug = re.sub(r"[._]+", "-", prefix)
    # Remove non-alphanumeric except hyphens
    slug = re.sub(r"[^a-z0-9-]", "", slug.lower())
    # Collapse multiple hyphens and strip
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "user"


class LocalUserStore:
    """SQLite-backed UserStore for development and single-user SaaS.

    Usage::

        store = LocalUserStore(Path("users.db"))
        user = store.create_user(
            email="alice@example.com",
            password_hash="$argon2id$...",
            name="Alice",
        )
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            self._migrate_schema(conn)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(_CREATE_SCHEMA)
        if version < 2:
            # Normalize existing emails to lowercase
            conn.execute("UPDATE users SET email = LOWER(TRIM(email))")
        if version < 3:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_token_balance (
                    user_id       TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    total_granted INTEGER NOT NULL DEFAULT 0,
                    total_used    INTEGER NOT NULL DEFAULT 0,
                    updated_at    TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS usage_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    operation     TEXT NOT NULL,
                    citekey       TEXT,
                    section       TEXT,
                    model         TEXT NOT NULL,
                    input_tokens  INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cost_usd      REAL,
                    created_at    TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_usage_log_user ON usage_log(user_id);
            """)
        if version < 4:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id  TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL DEFAULT 'dissertation',
                    created_at  TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
            """)
        if version < 5:
            conn.execute("ALTER TABLE projects ADD COLUMN outline TEXT DEFAULT NULL")
        if version < 6:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_reports (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id    TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    section       TEXT NOT NULL,
                    report_json   TEXT NOT NULL,
                    report_text   TEXT NOT NULL,
                    model         TEXT,
                    input_tokens  INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    created_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(project_id, section)
                );
                CREATE INDEX IF NOT EXISTS idx_rr_project ON research_reports(project_id);
            """)
        if version < 7:
            # Add username column — unique, used for git repo paths (user/project)
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)"
            )
            # Backfill existing users: generate username from email
            rows = conn.execute("SELECT user_id, email FROM users").fetchall()
            for row in rows:
                base = _username_from_email(row["email"])
                username = self._find_unique_username(conn, base)
                conn.execute(
                    "UPDATE users SET username = ? WHERE user_id = ?",
                    (username, row["user_id"]),
                )
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    # ------------------------------------------------------------------ #
    # UserStore Protocol implementation                                    #
    # ------------------------------------------------------------------ #

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        name: str = "",
    ) -> UserRecord:
        """Create a new user. Raises ValueError if email already exists.

        Username is auto-generated from email prefix (e.g. "ilya.b@gmail.com" → "ilya-b").
        If taken, appends random digits (e.g. "ilya-b-42").
        """
        user_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        email_normalized = email.strip().lower()
        base_username = _username_from_email(email_normalized)
        with self._conn() as conn:
            username = self._find_unique_username(conn, base_username)
            try:
                conn.execute(
                    """INSERT INTO users (user_id, email, password_hash, name, username, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, email_normalized, password_hash, name, username, now),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"User with email {email_normalized!r} already exists")
        return UserRecord(
            user_id=user_id,
            email=email,
            password_hash=password_hash,
            name=name,
            username=username,
            email_verified=False,
            created_at=now,
        )

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        """Look up a user by email. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        """Look up a user by ID. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def update_user(
        self,
        user_id: str,
        *,
        name: Optional[str] = None,
        email_verified: Optional[bool] = None,
    ) -> bool:
        """Update user fields. Returns True if user existed and was updated."""
        updates: list[str] = []
        params: list[object] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if email_verified is not None:
            updates.append("email_verified = ?")
            params.append(int(email_verified))
        if not updates:
            return False
        params.append(user_id)
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
        return cursor.rowcount > 0

    def store_refresh_token(
        self, user_id: str, token_hash: str, expires_at: str
    ) -> None:
        """Store a hashed refresh token for a user."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                   VALUES (?, ?, ?)""",
                (user_id, token_hash, expires_at),
            )

    def verify_refresh_token(self, user_id: str, token_hash: str) -> bool:
        """Check if a refresh token hash is valid (exists and not expired)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM refresh_tokens
                   WHERE user_id = ? AND token_hash = ? AND expires_at > ?""",
                (user_id, token_hash, now),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------ #
    # Project management                                                  #
    # ------------------------------------------------------------------ #

    def create_project(self, user_id: str, name: str, type_: str = "dissertation") -> dict:
        """Create a new project for a user. Returns the project dict."""
        project_id = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, user_id, name, type) VALUES (?, ?, ?, ?)",
                (project_id, user_id, name, type_),
            )
        return self.get_project_by_id(project_id)  # type: ignore[return-value]

    def get_projects(self, user_id: str) -> list[dict]:
        """List all projects for a user, ordered by creation date."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT project_id, name, type, created_at, outline FROM projects WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["outline"] = json.loads(d["outline"]) if d["outline"] else None
            result.append(d)
        return result

    def get_project_by_id(self, project_id: str) -> Optional[dict]:
        """Return project dict or None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT project_id, user_id, name, type, created_at, outline FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["outline"] = json.loads(d["outline"]) if d["outline"] else None
        return d

    def update_project_outline(self, project_id: str, sections: list[dict]) -> bool:
        """Save the outline (section list) for a project. Returns True if project existed."""
        outline_json = json.dumps(sections, ensure_ascii=False)
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE projects SET outline = ? WHERE project_id = ?",
                (outline_json, project_id),
            )
        return cursor.rowcount > 0

    def rename_project(self, project_id: str, name: str) -> bool:
        """Rename a project. Returns True if it existed."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE projects SET name = ? WHERE project_id = ?", (name, project_id)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ #
    # Research reports                                                      #
    # ------------------------------------------------------------------ #

    def save_research_report(
        self,
        project_id: str,
        section: str,
        report_json: str,
        report_text: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Save or replace a research report for a project section."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO research_reports
                   (project_id, section, report_json, report_text, model, input_tokens, output_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, section) DO UPDATE SET
                     report_json = excluded.report_json,
                     report_text = excluded.report_text,
                     model = excluded.model,
                     input_tokens = excluded.input_tokens,
                     output_tokens = excluded.output_tokens,
                     created_at = datetime('now')""",
                (project_id, section, report_json, report_text, model, input_tokens, output_tokens),
            )

    def get_research_report(self, project_id: str, section: str) -> dict | None:
        """Get the latest research report for a project section."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM research_reports WHERE project_id = ? AND section = ?",
                (project_id, section),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_project_research_reports(self, project_id: str) -> list[dict]:
        """Get all research reports for a project, ordered by section."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT section, created_at, model, input_tokens, output_tokens
                   FROM research_reports WHERE project_id = ? ORDER BY section""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_refresh_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user. Returns count revoked."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)
            )
        return cursor.rowcount

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Token balance & usage tracking                                       #
    # ------------------------------------------------------------------ #

    def get_token_balance(self, user_id: str) -> dict:
        """Get token balance for a user."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT total_granted, total_used FROM user_token_balance WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {"total_granted": 0, "total_used": 0, "remaining": 0}
        return {
            "total_granted": row["total_granted"],
            "total_used": row["total_used"],
            "remaining": max(0, row["total_granted"] - row["total_used"]),
        }

    def grant_tokens(self, user_id: str, amount: int) -> dict:
        """Grant tokens to a user (admin operation). Adds to total_granted."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_token_balance (user_id, total_granted, total_used, updated_at)
                   VALUES (?, ?, 0, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                     total_granted = total_granted + ?,
                     updated_at = datetime('now')""",
                (user_id, amount, amount),
            )
        return self.get_token_balance(user_id)

    def check_token_limit(self, user_id: str) -> bool:
        """Return True if user has tokens remaining."""
        bal = self.get_token_balance(user_id)
        return bal["remaining"] > 0

    def record_usage(
        self,
        user_id: str,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        citekey: str | None = None,
        section: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Record a token usage event and update balance."""
        total = input_tokens + output_tokens
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO usage_log
                   (user_id, operation, citekey, section, model, input_tokens, output_tokens, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, operation, citekey, section, model, input_tokens, output_tokens, cost_usd),
            )
            conn.execute(
                """INSERT INTO user_token_balance (user_id, total_granted, total_used, updated_at)
                   VALUES (?, 0, ?, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                     total_used = total_used + ?,
                     updated_at = datetime('now')""",
                (user_id, total, total),
            )

    def get_usage_summary(self, user_id: str) -> dict:
        """Get usage summary grouped by operation."""
        balance = self.get_token_balance(user_id)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT operation, COUNT(*) as count,
                   SUM(input_tokens + output_tokens) as tokens
                   FROM usage_log WHERE user_id = ?
                   GROUP BY operation""",
                (user_id,),
            ).fetchall()
        operations = [
            {"operation": r["operation"], "count": r["count"], "tokens": r["tokens"]}
            for r in rows
        ]
        return {**balance, "operations": operations}

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def get_user_by_username(self, username: str) -> Optional[UserRecord]:
        """Look up a user by username. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    @staticmethod
    def _find_unique_username(conn: sqlite3.Connection, base: str) -> str:
        """Find a unique username, appending random digits if base is taken."""
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (base,)
        ).fetchone()
        if not row:
            return base
        # Collision — append random digits
        for _ in range(100):
            candidate = f"{base}-{random.randint(10, 99)}"
            row = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (candidate,)
            ).fetchone()
            if not row:
                return candidate
        # Extremely unlikely fallback
        return f"{base}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            name=row["name"] or "",
            username=row["username"] or "",
            email_verified=bool(row["email_verified"]),
            created_at=row["created_at"] or "",
        )
