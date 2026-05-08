"""Tests for the weekly questions digest: data query + formatter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_format_digest_plain_no_html_tags():
    from src.telegram_handler import _format_questions_digest_plain

    rows = [{"chat_id": 111, "user_message": "Do kada PPDG-1R?", "created_at": "t"}]
    with patch("src.acl.get_name", return_value="Marko Petrović"):
        out = _format_questions_digest_plain(rows, days=7)
    assert "Pitanja u poslednjih 7 dana (1):" in out
    assert "Do kada PPDG-1R?" in out
    assert "Marko Petrović" in out
    # No HTML tags or escaping — this is for chat history, not Telegram.
    assert "<b>" not in out and "<i>" not in out
    assert "&lt;" not in out and "&gt;" not in out


def test_format_digest_plain_empty():
    from src.telegram_handler import _format_questions_digest_plain

    out = _format_questions_digest_plain([], days=7)
    assert "Pitanja u poslednjih 7 dana (0)" in out
    assert "Nema pitanja" in out


def test_format_digest_plain_preserves_special_chars():
    """Plain digest is for LLM context — raw chars must pass through verbatim."""
    from src.telegram_handler import _format_questions_digest_plain

    rows = [{"chat_id": 1, "user_message": "Šta je <PDV> & kako?", "created_at": "t"}]
    with patch("src.acl.get_name", return_value="Marko"):
        out = _format_questions_digest_plain(rows, days=1)
    assert "Šta je <PDV> & kako?" in out


def test_format_digest_plain_unknown_user():
    from src.telegram_handler import _format_questions_digest_plain

    rows = [{"chat_id": 999, "user_message": "anon", "created_at": "t"}]
    with patch("src.acl.get_name", return_value=None):
        out = _format_questions_digest_plain(rows, days=7)
    assert "ID 999" in out


async def test_pitanja_handler_injects_into_chat_history():
    """After /pitanja, the digest must land in chat history so follow-ups can reason over it."""
    from src import telegram_handler

    chat_id = 42
    rows = [
        {"chat_id": 100, "user_message": "Koja je stopa PDV?", "created_at": "2026-05-01T10:00:00"},
        {"chat_id": 200, "user_message": "Rok za PPDG-1R?", "created_at": "2026-05-02T11:00:00"},
    ]

    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = AsyncMock()

    context = MagicMock()
    context.args = ["7"]

    telegram_handler._chat_history.pop(chat_id, None)

    with (
        patch("src.telegram_handler._is_admin", return_value=True),
        patch(
            "src.telegram_handler.get_recent_questions",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch("src.acl.get_name", return_value="Korisnik"),
    ):
        await telegram_handler.pitanja_handler(update, context)

    history = telegram_handler._chat_history[chat_id]
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "/pitanja 7"}
    assert history[1]["role"] == "assistant"
    assert "Pitanja u poslednjih 7 dana (2):" in history[1]["content"]
    assert "Koja je stopa PDV?" in history[1]["content"]
    assert "Rok za PPDG-1R?" in history[1]["content"]
    # Plain text only — no HTML in chat history.
    assert "<b>" not in history[1]["content"]

    telegram_handler._chat_history.pop(chat_id, None)


async def test_pitanja_handler_no_injection_on_fetch_error():
    """If get_recent_questions fails, chat history must remain untouched."""
    from src import telegram_handler

    chat_id = 43
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = AsyncMock()

    context = MagicMock()
    context.args = []

    telegram_handler._chat_history.pop(chat_id, None)

    with (
        patch("src.telegram_handler._is_admin", return_value=True),
        patch(
            "src.telegram_handler.get_recent_questions",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
    ):
        await telegram_handler.pitanja_handler(update, context)

    assert chat_id not in telegram_handler._chat_history or not telegram_handler._chat_history[chat_id]


async def test_pitanja_handler_uses_default_days_in_synthetic_command():
    """When admin runs /pitanja without args, the synthetic user message should be '/pitanja' (no number)."""
    from src import telegram_handler

    chat_id = 44
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = AsyncMock()

    context = MagicMock()
    context.args = []

    telegram_handler._chat_history.pop(chat_id, None)

    with (
        patch("src.telegram_handler._is_admin", return_value=True),
        patch(
            "src.telegram_handler.get_recent_questions",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await telegram_handler.pitanja_handler(update, context)

    history = telegram_handler._chat_history[chat_id]
    assert history[0] == {"role": "user", "content": "/pitanja"}

    telegram_handler._chat_history.pop(chat_id, None)
