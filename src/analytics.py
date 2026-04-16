import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "analytics.db"

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_message TEXT NOT NULL,
    bot_response TEXT NOT NULL,
    used_web_search BOOLEAN DEFAULT 0,
    response_time_ms INTEGER,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_message TEXT NOT NULL,
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date DATE PRIMARY KEY,
    question_count INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,
    avg_response_time_ms REAL,
    escalation_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries(created_at);
CREATE INDEX IF NOT EXISTS idx_queries_chat_id ON queries(chat_id);
CREATE INDEX IF NOT EXISTS idx_escalations_created_at ON escalations(created_at);
"""


async def init_db() -> None:
    """Create tables if they don't exist and enable WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(_CREATE_TABLES)
        await db.commit()
    logger.info("Analytics DB initialized at %s", DB_PATH)


async def log_query(
    chat_id: int,
    user_message: str,
    bot_response: str,
    used_web_search: bool,
    response_time_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Insert a query record. Fire-and-forget — errors are logged, not raised."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO queries
                   (chat_id, user_message, bot_response, used_web_search,
                    response_time_ms, input_tokens, output_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, user_message, bot_response, used_web_search,
                 response_time_ms, input_tokens, output_tokens),
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to log query for chat_id=%s", chat_id)


async def log_escalation(chat_id: int, user_message: str, reason: str) -> None:
    """Insert an escalation record."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO escalations (chat_id, user_message, reason) VALUES (?, ?, ?)",
                (chat_id, user_message, reason),
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to log escalation for chat_id=%s", chat_id)


async def get_stats(days: int = 7) -> dict:
    """Return stats dict for the last N days."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        prev_cutoff = (datetime.utcnow() - timedelta(days=days * 2)).isoformat()
        now = datetime.utcnow().isoformat()

        # Current period
        row = await db.execute_fetchall(
            """SELECT COUNT(*) as cnt, COUNT(DISTINCT chat_id) as users,
                      AVG(response_time_ms) as avg_ms
               FROM queries WHERE created_at >= ?""",
            (cutoff,),
        )
        question_count = row[0][0] or 0
        unique_users = row[0][1] or 0
        avg_response_time_ms = row[0][2] or 0

        # Escalations current period
        esc_row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM escalations WHERE created_at >= ?",
            (cutoff,),
        )
        escalation_count = esc_row[0][0] or 0
        answered_count = question_count  # all queries are answered (escalations are separate)

        # Previous period for week-over-week
        prev_row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM queries WHERE created_at >= ? AND created_at < ?",
            (prev_cutoff, cutoff),
        )
        prev_count = prev_row[0][0] or 0

        if prev_count > 0:
            wow_change = round((question_count - prev_count) / prev_count * 100)
        else:
            wow_change = None  # no previous data

        # Recent escalations (newest first)
        esc_rows = await db.execute_fetchall(
            """SELECT reason, created_at
               FROM escalations WHERE created_at >= ?
               ORDER BY created_at DESC LIMIT 5""",
            (cutoff,),
        )
        recent_escalations = [
            {"reason": r[0] or "nepoznato", "created_at": r[1]} for r in esc_rows
        ]

        return {
            "days": days,
            "question_count": question_count,
            "unique_users": unique_users,
            "answered_count": answered_count,
            "escalation_count": escalation_count,
            "avg_response_time_ms": avg_response_time_ms,
            "recent_escalations": recent_escalations,
            "week_over_week_change": wow_change,
        }


async def update_daily_stats(date: str) -> None:
    """Aggregate queries/escalations into daily_stats for the given date (YYYY-MM-DD)."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            row = await db.execute_fetchall(
                """SELECT COUNT(*), COUNT(DISTINCT chat_id), AVG(response_time_ms)
                   FROM queries
                   WHERE date(created_at) = ?""",
                (date,),
            )
            q_count, users, avg_ms = row[0]

            esc_row = await db.execute_fetchall(
                "SELECT COUNT(*) FROM escalations WHERE date(created_at) = ?",
                (date,),
            )
            esc_count = esc_row[0][0]

            await db.execute(
                """INSERT OR REPLACE INTO daily_stats
                   (date, question_count, unique_users, avg_response_time_ms, escalation_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (date, q_count or 0, users or 0, avg_ms, esc_count or 0),
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to update daily stats for %s", date)
