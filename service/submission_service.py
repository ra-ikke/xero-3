"""SQLite-backed storage for map submission sessions.

This module is intentionally sync (sqlite3). Operations are small and fast.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from resources.category_list import CATEGORY_LIST

logger = logging.getLogger(__name__)


SUBMISSION_LIMIT_DEFAULT = 3
SUBMISSION_LIMIT_OVERRIDES: dict[str, int] = {
    # category_code -> per-user valid submission limit
    # Add overrides here if needed.
}


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _db_default_path() -> str:
    # xero3.0/service/ -> xero3.0/data/submissions.db
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "data", "submissions.db")


@dataclass(frozen=True)
class SubmissionInsertResult:
    inserted: bool
    is_valid: bool
    reason: Optional[str]


class SubmissionService:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _db_default_path()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON;")
            con.execute("PRAGMA journal_mode = WAL;")
        except Exception:
            # Non-fatal (older SQLite builds / permissions)
            pass
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_code TEXT NOT NULL,
                    session_no INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    UNIQUE(category_code, session_no),
                    UNIQUE(thread_id)
                );

                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_tag TEXT NOT NULL,
                    map_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_valid INTEGER NOT NULL,
                    reason TEXT,
                    UNIQUE(session_id, user_id, map_code),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_category_status
                    ON sessions(category_code, status);
                CREATE INDEX IF NOT EXISTS idx_submissions_session_user
                    ON submissions(session_id, user_id);
                """
            )

    def submission_limit_for_category(self, category_code: str) -> int:
        """Returns the per-user submission limit for a category.

        Precedence:
        1) SUBMISSION_LIMIT_OVERRIDES (hard override)
        2) resources.category_list.CATEGORY_LIST[*]["submissionlimit"]
        3) SUBMISSION_LIMIT_DEFAULT
        """
        if category_code in SUBMISSION_LIMIT_OVERRIDES:
            return int(SUBMISSION_LIMIT_OVERRIDES[category_code])

        cat = next((c for c in CATEGORY_LIST if c.get("name") == category_code), None)
        raw = None
        if cat:
            # New preferred attribute name.
            raw = cat.get("submissionlimit", None)
            # Backward compatible alias.
            if raw is None:
                raw = cat.get("maplimit", None)
        try:
            if raw is not None:
                value = int(raw)
                if value <= 0:
                    return 0
                return value
        except Exception:
            # Fall back to default.
            pass

        return int(SUBMISSION_LIMIT_DEFAULT)

    # -------------------------
    # Sessions
    # -------------------------
    def get_active_session(self, category_code: str) -> Optional[sqlite3.Row]:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM sessions WHERE category_code=? AND status='active' ORDER BY id DESC LIMIT 1",
                (category_code,),
            ).fetchone()

    def get_active_session_by_thread(self, thread_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM sessions WHERE thread_id=? AND status='active' LIMIT 1",
                (int(thread_id),),
            ).fetchone()

    def get_next_session_no(self, category_code: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(session_no), 0) AS max_no FROM sessions WHERE category_code=?",
                (category_code,),
            ).fetchone()
            return int(row["max_no"] or 0) + 1

    def start_session(self, *, category_code: str, thread_id: int) -> sqlite3.Row:
        existing = self.get_active_session(category_code)
        if existing:
            return existing

        session_no = self.get_next_session_no(category_code)
        started_at = _utc_now_iso()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO sessions(category_code, session_no, thread_id, started_at, ended_at, status)
                VALUES (?, ?, ?, ?, NULL, 'active')
                """,
                (category_code, int(session_no), int(thread_id), started_at),
            )
            session_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            return con.execute("SELECT * FROM sessions WHERE id=?", (int(session_id),)).fetchone()

    def end_session(self, *, session_id: int) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE sessions SET status='ended', ended_at=? WHERE id=?",
                (_utc_now_iso(), int(session_id)),
            )

    # -------------------------
    # Submissions
    # -------------------------
    def _insert_submission_with_con(
        self,
        *,
        con: sqlite3.Connection,
        session_id: int,
        user_id: int,
        user_tag: str,
        map_code: str,
        is_valid: bool,
        reason: Optional[str],
        created_at: str,
    ) -> SubmissionInsertResult:
        try:
            con.execute(
                """
                INSERT INTO submissions(session_id, user_id, user_tag, map_code, created_at, is_valid, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(session_id),
                    int(user_id),
                    str(user_tag),
                    str(map_code),
                    str(created_at),
                    1 if is_valid else 0,
                    reason,
                ),
            )
            return SubmissionInsertResult(inserted=True, is_valid=is_valid, reason=reason)
        except sqlite3.IntegrityError:
            return SubmissionInsertResult(inserted=False, is_valid=is_valid, reason="duplicate")

    def _valid_submission_count(self, *, con: sqlite3.Connection, session_id: int, user_id: int) -> int:
        row = con.execute(
            "SELECT COUNT(*) AS c FROM submissions WHERE session_id=? AND user_id=? AND is_valid=1",
            (int(session_id), int(user_id)),
        ).fetchone()
        return int(row["c"] or 0)

    def add_submission(
        self,
        *,
        session_id: int,
        user_id: int,
        user_tag: str,
        map_code: str,
        is_valid: bool,
        reason: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> SubmissionInsertResult:
        created_at = created_at or _utc_now_iso()
        with self._connect() as con:
            return self._insert_submission_with_con(
                con=con,
                session_id=session_id,
                user_id=user_id,
                user_tag=user_tag,
                map_code=map_code,
                is_valid=is_valid,
                reason=reason,
                created_at=str(created_at),
            )

    def add_submission_enforcing_limit(
        self,
        *,
        category_code: str,
        session_id: int,
        user_id: int,
        user_tag: str,
        map_code: str,
    ) -> SubmissionInsertResult:
        limit = self.submission_limit_for_category(category_code)
        with self._connect() as con:
            if limit <= 0:
                return self._insert_submission_with_con(
                    con=con,
                    session_id=session_id,
                    user_id=user_id,
                    user_tag=user_tag,
                    map_code=map_code,
                    is_valid=True,
                    reason=None,
                    created_at=_utc_now_iso(),
                )
            current_valid = self._valid_submission_count(con=con, session_id=session_id, user_id=user_id)
            if current_valid >= limit:
                return self._insert_submission_with_con(
                    con=con,
                    session_id=session_id,
                    user_id=user_id,
                    user_tag=user_tag,
                    map_code=map_code,
                    is_valid=False,
                    reason=f"limit_exceeded:{limit}",
                    created_at=_utc_now_iso(),
                )
            return self._insert_submission_with_con(
                con=con,
                session_id=session_id,
                user_id=user_id,
                user_tag=user_tag,
                map_code=map_code,
                is_valid=True,
                reason=None,
                created_at=_utc_now_iso(),
            )

    def iter_submissions(self, *, session_id: int) -> Iterable[sqlite3.Row]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM submissions WHERE session_id=? ORDER BY id ASC",
                (int(session_id),),
            ).fetchall()
            return rows

    def export_session_txt(self, *, session_id: int) -> tuple[str, bytes]:
        """Exports a session as a TXT file (filename, bytes).

        The format is designed to be used by an external tool:
        - The exported file has no per-map comments.
        - The tool can add comments and decision codes and upload it back.
        """
        with self._connect() as con:
            session = con.execute("SELECT * FROM sessions WHERE id=?", (int(session_id),)).fetchone()
            if not session:
                raise ValueError("Session not found.")
            subs = con.execute(
                "SELECT * FROM submissions WHERE session_id=? ORDER BY id ASC",
                (int(session_id),),
            ).fetchall()

        category_code = str(session["category_code"])
        limit = self.submission_limit_for_category(category_code)

        lines: list[str] = []
        lines.append("####SAVED SESSION####")

        good_count = 0
        idx = 0
        for s in subs:
            idx += 1
            user_tag = str(s["user_tag"] or "Unknown")
            map_code = str(s["map_code"] or "").strip()
            if not map_code:
                continue

            is_valid = int(s["is_valid"] or 0) == 1
            if is_valid:
                good_count += 1
                # GOOD format: GOOD:<pos>|<author>|<code>|<extra>|<decision>|<comment>
                # We export with decision=0 and empty comment.
                lines.append(f"GOOD:{idx}|{user_tag}|{map_code}|++++++|0|")
            else:
                reason = str(s["reason"] or "").strip()
                # Keep reason short and without forum links.
                if reason.startswith("limit_exceeded:"):
                    reason_text = f"Posted more than {limit} maps"
                else:
                    reason_text = reason or "Ignored"
                lines.append(f"BAD:{idx}|{user_tag}|{map_code}|++++++|0|{reason_text}")

        lines.append(f"POS:{good_count}")
        lines.append(f"CAT:{category_code}")
        lines.append("")

        # backup_<CAT>_<YYYY-MM-DD>.txt
        today = datetime.utcnow().date().isoformat()
        filename = f"backup_{category_code}_{today}.txt"
        return filename, ("\n".join(lines)).encode("utf-8")


_service_singleton: Optional[SubmissionService] = None


def get_submission_service() -> SubmissionService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = SubmissionService()
        logger.info("SubmissionService initialized at %s", _service_singleton.db_path)
    return _service_singleton

