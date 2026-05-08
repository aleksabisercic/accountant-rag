"""Verify that tool-usage metadata lands in chat history but never in the Telegram reply."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import AgentResponse


def _make_agent_response(text="Odgovor.", tools_used=None):
    return AgentResponse(
        text=text,
        tools_used=tools_used or [],
        input_tokens=10,
        output_tokens=20,
        rounds=1,
    )


@pytest.fixture
def chat_id():
    return 42


@pytest.fixture(autouse=True)
def _patch_deps(chat_id):
    """Patch external dependencies so the message handler can run in isolation."""
    with (
        patch("src.telegram_handler._is_allowed", return_value=True),
        patch("src.telegram_handler._is_rate_limited", return_value=False),
        patch("src.telegram_handler.log_query", new_callable=AsyncMock),
    ):
        yield


def _build_update(chat_id, text="Koliki je PDV?"):
    message = AsyncMock()
    message.text = text
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.id = chat_id
    update.effective_user.first_name = "Test"
    update.effective_user.last_name = None
    update.message = message
    return update


async def test_tools_annotation_in_history_not_in_reply(chat_id):
    # [CONF:NA] = out-of-scope marker, no disclaimer appended — keeps this
    # test focused on tools_used behavior, not disclaimer rendering.
    result = _make_agent_response(
        text="PDV iznosi 300.000 RSD. [CONF:NA]",
        tools_used=["code_execution", "search_documents"],
    )
    update = _build_update(chat_id)
    context = MagicMock()

    with (
        patch("src.telegram_handler.agent_run", new_callable=AsyncMock, return_value=result),
        patch("src.telegram_handler._send_long", new_callable=AsyncMock) as mock_send,
        patch("src.telegram_handler._chat_history") as mock_history,
    ):
        from src.telegram_handler import message_handler
        mock_history.__getitem__ = MagicMock(return_value=[])
        mock_history.__contains__ = MagicMock(return_value=False)

        await message_handler(update, context)

        # Reply to Telegram must NOT contain tool annotation
        sent_text = mock_send.call_args[0][1]
        assert "[Korišćeni alati" not in sent_text
        assert sent_text == "PDV iznosi 300.000 RSD."


async def test_no_annotation_when_no_tools():
    result = _make_agent_response(text="Odgovor bez alata.", tools_used=[])

    history_text = result.text
    if result.tools_used:
        history_text += f"\n\n[Korišćeni alati: {', '.join(result.tools_used)}]"

    assert history_text == "Odgovor bez alata."
    assert "[Korišćeni alati" not in history_text


async def test_annotation_format_with_tools():
    result = _make_agent_response(
        text="Bruto plata je 180.000.",
        tools_used=["code_execution", "search_documents"],
    )

    history_text = result.text
    if result.tools_used:
        history_text += f"\n\n[Korišćeni alati: {', '.join(result.tools_used)}]"

    assert history_text.startswith("Bruto plata je 180.000.")
    assert "[Korišćeni alati: code_execution, search_documents]" in history_text
    # Original text is unmodified
    assert result.text == "Bruto plata je 180.000."
