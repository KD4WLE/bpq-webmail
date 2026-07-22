"""Privacy-conscious usage analytics for BPQ Portal."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


BOT_TOKENS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "bingpreview",
    "facebookexternalhit",
    "monitoring",
    "uptimerobot",
)


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            callsign TEXT,
            visitor_key TEXT,
            path TEXT NOT NULL,
            method TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_ms REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'portal'
        )
        """
    )

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(usage_requests)").fetchall()
    }
    if "source" not in columns:
        conn.execute(
            "ALTER TABLE usage_requests ADD COLUMN source TEXT NOT NULL DEFAULT 'portal'"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_nginx_imports (
            fingerprint TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_created "
        "ON usage_requests(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_user "
        "ON usage_requests(user_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_path "
        "ON usage_requests(path, created_at)"
    )
    conn.commit()


def is_ignored_path(path: str, ignored_paths: Iterable[str]) -> bool:
    for item in ignored_paths:
        item = item.strip()
        if not item:
            continue
        if item.endswith("*") and path.startswith(item[:-1]):
            return True
        if path == item or path.startswith(item.rstrip("/") + "/"):
            return True
    return False


def is_bot(user_agent: str) -> bool:
    value = (user_agent or "").lower()
    return any(token in value for token in BOT_TOKENS)


def visitor_key(client_ip: str, user_agent: str, secret: str) -> str:
    material = f"{secret}|{client_ip}|{user_agent}".encode(
        "utf-8", errors="ignore"
    )
    return hashlib.sha256(material).hexdigest()[:24]


def record_request(
    db_path: Path | str,
    *,
    path: str,
    method: str,
    status_code: int,
    response_ms: float,
    user_id: int | None,
    callsign: str | None,
    client_ip: str,
    user_agent: str,
    secret: str,
    source: str = "portal",
) -> None:
    anonymous_key = None
    if user_id is None:
        anonymous_key = visitor_key(client_ip, user_agent, secret)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO usage_requests (
                user_id,
                callsign,
                visitor_key,
                path,
                method,
                status_code,
                response_ms,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                callsign,
                anonymous_key,
                path[:255],
                method[:12],
                int(status_code),
                float(response_ms),
                source[:24],
            ),
        )
        conn.commit()


def prune_old_requests(db_path: Path | str, retention_days: int) -> int:
    if retention_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    with connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM usage_requests WHERE created_at < ?",
            (cutoff_text,),
        )
        conn.commit()
        return max(cursor.rowcount, 0)


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_usage_report(db_path: Path | str, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 3650))
    window = f"-{days} days"

    with connect(db_path) as conn:
        init_db(conn)

        summary = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COUNT(DISTINCT CASE
                        WHEN user_id IS NOT NULL THEN 'u:' || user_id
                        ELSE 'v:' || visitor_key
                    END) AS unique_visitors,
                    COUNT(DISTINCT user_id) AS active_users,
                    ROUND(AVG(response_ms), 1) AS avg_response_ms,
                    SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END)
                        AS server_errors
                FROM usage_requests
                WHERE created_at >= datetime('now', ?)
                """,
                (window,),
            ).fetchone()
        )

        daily = _rows(
            conn,
            """
            WITH RECURSIVE dates(day) AS (
                SELECT date('now', ?)
                UNION ALL
                SELECT date(day, '+1 day')
                FROM dates
                WHERE day < date('now')
            )
            SELECT
                dates.day,
                COUNT(r.id) AS requests,
                COUNT(DISTINCT CASE
                    WHEN r.user_id IS NOT NULL THEN 'u:' || r.user_id
                    ELSE 'v:' || r.visitor_key
                END) AS visitors
            FROM dates
            LEFT JOIN usage_requests r ON date(r.created_at) = dates.day
            GROUP BY dates.day
            ORDER BY dates.day
            """,
            (f"-{days - 1} days",),
        )

        top_pages = _rows(
            conn,
            """
            SELECT
                path,
                COUNT(*) AS requests,
                ROUND(AVG(response_ms), 1) AS avg_response_ms
            FROM usage_requests
            WHERE created_at >= datetime('now', ?)
              AND method = 'GET'
            GROUP BY path
            ORDER BY requests DESC, path
            LIMIT 15
            """,
            (window,),
        )

        top_users = _rows(
            conn,
            """
            SELECT
                COALESCE(NULLIF(callsign, ''), 'User ' || user_id) AS callsign,
                COUNT(*) AS requests,
                COUNT(DISTINCT date(created_at)) AS active_days,
                MAX(created_at) AS last_seen
            FROM usage_requests
            WHERE created_at >= datetime('now', ?)
              AND user_id IS NOT NULL
            GROUP BY user_id, callsign
            ORDER BY requests DESC
            LIMIT 15
            """,
            (window,),
        )

        recent_users = _rows(
            conn,
            """
            SELECT
                COALESCE(NULLIF(callsign, ''), 'User ' || user_id) AS callsign,
                COUNT(*) AS requests,
                COUNT(DISTINCT path) AS pages,
                MAX(created_at) AS last_seen,
                (
                    SELECT inner_requests.path
                    FROM usage_requests inner_requests
                    WHERE inner_requests.user_id = usage_requests.user_id
                      AND inner_requests.created_at >= datetime('now', ?)
                    ORDER BY inner_requests.created_at DESC, inner_requests.id DESC
                    LIMIT 1
                ) AS last_path
            FROM usage_requests
            WHERE created_at >= datetime('now', ?)
              AND user_id IS NOT NULL
            GROUP BY user_id, callsign
            ORDER BY last_seen DESC
            LIMIT 20
            """,
            (window, window),
        )

        status_codes = _rows(
            conn,
            """
            SELECT status_code, COUNT(*) AS requests
            FROM usage_requests
            WHERE created_at >= datetime('now', ?)
            GROUP BY status_code
            ORDER BY requests DESC
            """,
            (window,),
        )

    return {
        "summary": summary,
        "daily": daily,
        "top_pages": top_pages,
        "top_users": top_users,
        "recent_users": recent_users,
        "status_codes": status_codes,
    }
