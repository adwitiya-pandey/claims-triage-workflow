"""SQLite persistence for decisions and review states per SPEC 13."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
  claim_id        TEXT NOT NULL,
  version         INTEGER NOT NULL,
  route_recommended TEXT NOT NULL CHECK (route_recommended IN ('FAST_TRACK','STANDARD_REVIEW','ESCALATE')),
  route_final     TEXT NOT NULL CHECK (route_final IN ('FAST_TRACK','STANDARD_REVIEW','ESCALATE')),
  record_json     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  PRIMARY KEY (claim_id, version)
);
CREATE TABLE IF NOT EXISTS reviews (
  thread_id    TEXT PRIMARY KEY,
  claim_id     TEXT NOT NULL,
  version      INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('awaiting','done')),
  created_at   TEXT NOT NULL,
  resolved_at  TEXT,
  reviewer_id  TEXT
);
"""


def _get_connection(db_path: Path | str) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str) -> None:
    """Initialize triage database schema."""
    conn = _get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def save_decision(db_path: Path | str, decision: dict[str, Any]) -> None:
    """Save an immutable decision record using parameterised INSERT OR IGNORE."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        record_json = json.dumps(decision, ensure_ascii=False)
        conn.execute(
            """INSERT OR IGNORE INTO decisions
            (claim_id, version, route_recommended, route_final, record_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                decision["claim_id"],
                int(decision["version"]),
                decision["route_recommended"],
                decision["route_final"],
                record_json,
                decision.get("created_at", datetime.now(UTC).isoformat()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_decision(db_path: Path | str, claim_id: str) -> dict[str, Any] | None:
    """Retrieve the latest decision record for a claim."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        cur = conn.execute(
            "SELECT record_json FROM decisions WHERE claim_id = ? ORDER BY version DESC LIMIT 1",
            (claim_id,),
        )
        row = cur.fetchone()
        if row:
            return json.loads(row["record_json"])
        return None
    finally:
        conn.close()


def get_decision(db_path: Path | str, claim_id: str, version: int) -> dict[str, Any] | None:
    """Retrieve a specific version of a decision record."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        cur = conn.execute(
            "SELECT record_json FROM decisions WHERE claim_id = ? AND version = ?",
            (claim_id, version),
        )
        row = cur.fetchone()
        if row:
            return json.loads(row["record_json"])
        return None
    finally:
        conn.close()


def list_decisions(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """List all decision versions for a claim in ascending order."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        cur = conn.execute(
            "SELECT record_json FROM decisions WHERE claim_id = ? ORDER BY version ASC",
            (claim_id,),
        )
        return [json.loads(row["record_json"]) for row in cur.fetchall()]
    finally:
        conn.close()


def save_review_awaiting(db_path: Path | str, thread_id: str, claim_id: str, version: int) -> None:
    """Record an awaiting review session."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO reviews
            (thread_id, claim_id, version, status, created_at)
            VALUES (?, ?, ?, 'awaiting', ?)""",
            (thread_id, claim_id, version, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_awaiting_review(db_path: Path | str, claim_id: str) -> dict[str, Any] | None:
    """Check if there is an awaiting review for a claim."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        cur = conn.execute(
            "SELECT * FROM reviews WHERE claim_id = ? AND status = 'awaiting' ORDER BY version DESC LIMIT 1",
            (claim_id,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def resolve_review(db_path: Path | str, thread_id: str, reviewer_id: str) -> None:
    """Mark a review session as done."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE reviews SET status = 'done', resolved_at = ?, reviewer_id = ? WHERE thread_id = ?",
            (now, reviewer_id, thread_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_pending_reviews(db_path: Path | str) -> list[dict[str, Any]]:
    """List all currently pending (awaiting) reviews."""
    conn = _get_connection(db_path)
    try:
        init_db(db_path)
        cur = conn.execute(
            "SELECT * FROM reviews WHERE status = 'awaiting' ORDER BY created_at ASC"
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
