"""Regression: /stats recent escalations must show the user's QUESTION.

The escalation reason column embeds a preview of the bot's answer
("CONF:LOW | <reply…>") — displaying it verbatim showed answers instead of
questions. get_stats() must surface user_message and keep only the CONF tag.
"""

from unittest.mock import patch

from src import analytics
from src.telegram_handler import _format_stats


async def test_recent_escalations_expose_question_not_answer(tmp_path):
    db_path = tmp_path / "analytics.db"
    with patch.object(analytics, "DB_PATH", db_path):
        await analytics.init_db()
        await analytics.log_escalation(
            chat_id=42,
            user_message="Šta je aktiva?",
            reason="CONF:LOW | Aktiva predstavlja jednu stranu bilansa stanja",
        )

        stats = await analytics.get_stats(days=7)

    assert stats["escalation_count"] == 1
    esc = stats["recent_escalations"][0]
    assert esc["question"] == "Šta je aktiva?"
    assert esc["conf"] == "CONF:LOW"
    assert "Aktiva predstavlja" not in str(esc.values())


async def test_format_stats_renders_question_line(tmp_path):
    stats = {
        "question_count": 3,
        "week_over_week_change": None,
        "answered_count": 2,
        "escalation_count": 1,
        "unique_users": 2,
        "avg_response_time_ms": 12000,
        "recent_escalations": [
            {
                "question": "Koji je rok za PDV prijavu?",
                "conf": "CONF:LOW",
                "created_at": "2026-07-10 19:05:00",
            }
        ],
    }

    text = _format_stats(stats, header="Statistika")

    assert "CONF:LOW | Koji je rok za PDV prijavu? (2026-07-10 19:05)" in text


async def test_format_stats_truncates_long_questions():
    long_q = "x" * 100
    stats = {
        "question_count": 1,
        "week_over_week_change": None,
        "answered_count": 0,
        "escalation_count": 1,
        "unique_users": 1,
        "avg_response_time_ms": 0,
        "recent_escalations": [
            {"question": long_q, "conf": "CONF:NONE", "created_at": "2026-07-10 19:05:00"}
        ],
    }

    text = _format_stats(stats, header="Statistika")

    assert "x" * 60 + "…" in text
    assert "x" * 61 not in text
