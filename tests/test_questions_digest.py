"""Tests for the weekly questions digest: data query + formatter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import aiosqlite
import pytest


@pytest.fixture
async def analytics_db(tmp_path):
    """Patch DB_PATH to a temp SQLite and initialize the schema."""
    db_path = tmp_path / "analytics.db"
    with patch("src.analytics.DB_PATH", db_path):
        from src.analytics import init_db
        await init_db()
        yield db_path


async def _insert_query(db_path, chat_id: int, message: str, when: datetime) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO queries
               (chat_id, user_message, bot_response, used_web_search,
                response_time_ms, input_tokens, output_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, message, "reply", False, 100, 10, 10, when.isoformat()),
        )
        await db.commit()


async def test_get_recent_questions_filters_by_window(analytics_db):
    from src.analytics import get_recent_questions

    now = datetime.now(timezone.utc)
    await _insert_query(analytics_db, 1, "today", now - timedelta(hours=1))
    await _insert_query(analytics_db, 2, "three days ago", now - timedelta(days=3))
    await _insert_query(analytics_db, 3, "ten days ago", now - timedelta(days=10))

    rows = await get_recent_questions(days=7)
    messages = [r["user_message"] for r in rows]
    assert messages == ["three days ago", "today"]  # ASC by created_at


async def test_get_recent_questions_empty_window(analytics_db):
    from src.analytics import get_recent_questions

    rows = await get_recent_questions(days=7)
    assert rows == []


async def test_get_recent_questions_preserves_chat_id(analytics_db):
    from src.analytics import get_recent_questions

    now = datetime.now(timezone.utc)
    await _insert_query(analytics_db, 42, "pitanje", now - timedelta(hours=2))

    rows = await get_recent_questions(days=1)
    assert rows[0]["chat_id"] == 42
    assert rows[0]["user_message"] == "pitanje"
    assert rows[0]["created_at"]  # non-empty ISO timestamp


def test_format_digest_empty():
    from src.telegram_handler import _format_questions_digest

    out = _format_questions_digest([], days=7)
    assert "Pitanja u poslednjih 7 dana (0)" in out
    assert "Nema pitanja" in out


def test_format_digest_with_named_user():
    from src.telegram_handler import _format_questions_digest

    rows = [{"chat_id": 111, "user_message": "Do kada PPDG-1R?", "created_at": "t"}]
    with patch("src.acl.get_name", return_value="Marko Petrović"):
        out = _format_questions_digest(rows, days=7)
    assert "Do kada PPDG-1R?" in out
    assert "Marko Petrović" in out
    assert "(1)" in out  # count in header


def test_format_digest_with_unknown_user():
    from src.telegram_handler import _format_questions_digest

    rows = [{"chat_id": 999, "user_message": "anon", "created_at": "t"}]
    with patch("src.acl.get_name", return_value=None):
        out = _format_questions_digest(rows, days=7)
    assert "ID 999" in out


def test_format_digest_html_escape():
    from src.telegram_handler import _format_questions_digest

    rows = [{"chat_id": 1, "user_message": "<script>x</script>", "created_at": "t"}]
    with patch("src.acl.get_name", return_value="<bad>"):
        out = _format_questions_digest(rows, days=1)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;bad&gt;" in out


def test_format_digest_truncates_long_message():
    from src.telegram_handler import _QUESTION_PREVIEW_MAX, _format_questions_digest

    long_q = "x" * (_QUESTION_PREVIEW_MAX + 100)
    rows = [{"chat_id": 1, "user_message": long_q, "created_at": "t"}]
    with patch("src.acl.get_name", return_value="A"):
        out = _format_questions_digest(rows, days=1)
    assert "…" in out
    # Full raw question should not appear — only the truncated prefix.
    assert long_q not in out
