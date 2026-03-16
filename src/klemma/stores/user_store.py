"""LocalUserStore — SQLite implementation of the UserStore protocol (ADR-009).

Stores user accounts and refresh tokens for the SaaS auth layer.
Separate DB file (users.db) from the library data.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from ..models import UserRecord

_SCHEMA_VERSION = 3

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
        """Create a new user. Raises ValueError if email already exists."""
        user_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        email_normalized = email.strip().lower()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO users (user_id, email, password_hash, name, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (user_id, email_normalized, password_hash, name, now),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"User with email {email_normalized!r} already exists")
        return UserRecord(
            user_id=user_id,
            email=email,
            password_hash=password_hash,
            name=name,
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

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            name=row["name"] or "",
            email_verified=bool(row["email_verified"]),
            created_at=row["created_at"] or "",
        )
