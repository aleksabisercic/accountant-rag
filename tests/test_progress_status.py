"""Progress status updates: agent on_progress callback + Telegram status message."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import AgentResponse
from src.telegram_handler import (
    _STATUS_BY_TOOL,
    _STATUS_DEFAULT,
    _STATUS_INITIAL,
    _status_for_tools,
)


# --- _status_for_tools mapping ---


def test_search_documents_status():
    assert _status_for_tools(["search_documents"]) == "📚 Pretražujem dokumentaciju…"


def test_search_documents_takes_priority_over_web_search():
    status = _status_for_tools(["search_documents", "web_search"])
    assert status == "📚 Pretražujem dokumentaciju…"


def test_no_tools_falls_back_to_default():
    assert _status_for_tools([]) == _STATUS_DEFAULT


def test_unknown_tool_falls_back_to_default():
    assert _status_for_tools(["something_new"]) == _STATUS_DEFAULT


def test_no_status_mentions_internet():
    """Client constraint: user-facing statuses must never reveal internet/web usage."""
    all_statuses = [_STATUS_INITIAL, _STATUS_DEFAULT] + [s for _, s in _STATUS_BY_TOOL]
    for status in all_statuses:
        lowered = status.lower()
        for forbidden in ("internet", "web", "mrež", "online", "google"):
            assert forbidden not in lowered, f"{status!r} leaks internet usage"


# --- agent.run invokes on_progress ---


def _text_block(text):
    return SimpleNamespace(type="text", text=text, citations=None)


def _search_tool_block(query="pdv stopa", block_id="tu_1"):
    return SimpleNamespace(
        type="tool_use", name="search_documents", input={"query": query}, id=block_id
    )


def _response(content, stop_reason):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def _agent_two_rounds():
    """One search_documents round, then end_turn."""
    responses = [
        _response([_search_tool_block()], stop_reason="tool_use"),
        _response([_text_block("Odgovor. [CONF:HIGH]")], stop_reason="end_turn"),
    ]
    empty_search = json.dumps({"results": []})
    with (
        patch("src.agent._api_call_with_retry", AsyncMock(side_effect=responses)),
        patch("src.agent._handle_search", AsyncMock(return_value=empty_search)),
        patch("src.agent._load_system_prompt", return_value="system"),
    ):
        yield


async def test_on_progress_called_per_tool_round(_agent_two_rounds):
    from src.agent import run

    on_progress = AsyncMock()
    result = await run([{"role": "user", "content": "pitanje"}], on_progress=on_progress)

    on_progress.assert_awaited_once_with(1, ["search_documents"])
    assert result.text == "Odgovor. [CONF:HIGH]"
    assert result.rounds == 2


async def test_on_progress_error_does_not_break_run(_agent_two_rounds):
    from src.agent import run

    on_progress = AsyncMock(side_effect=RuntimeError("edit failed"))
    result = await run([{"role": "user", "content": "pitanje"}], on_progress=on_progress)

    assert result.text == "Odgovor. [CONF:HIGH]"


async def test_run_without_callback_unchanged(_agent_two_rounds):
    from src.agent import run

    result = await run([{"role": "user", "content": "pitanje"}])
    assert result.text == "Odgovor. [CONF:HIGH]"


# --- message_handler status message lifecycle ---


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


@pytest.fixture(autouse=True)
def _patch_handler_deps():
    with (
        patch("src.telegram_handler._is_allowed", return_value=True),
        patch("src.telegram_handler._is_rate_limited", return_value=False),
        patch("src.telegram_handler.log_query", new_callable=AsyncMock),
    ):
        yield


async def test_status_message_sent_edited_and_deleted():
    update = _build_update(42)
    status_msg = AsyncMock()
    update.message.reply_text.return_value = status_msg

    async def fake_agent_run(messages, on_progress=None):
        await on_progress(1, ["search_documents"])
        await on_progress(2, ["search_documents"])  # duplicate → no second edit
        return AgentResponse(text="Odgovor. [CONF:NA]", rounds=3)

    with (
        patch("src.telegram_handler.agent_run", side_effect=fake_agent_run),
        patch("src.telegram_handler._send_long", new_callable=AsyncMock),
        patch("src.telegram_handler._chat_history") as mock_history,
    ):
        from src.telegram_handler import message_handler
        mock_history.__getitem__ = MagicMock(return_value=[])

        await message_handler(update, MagicMock())

    update.message.reply_text.assert_awaited_once_with(_STATUS_INITIAL)
    status_msg.edit_text.assert_awaited_once_with("📚 Pretražujem dokumentaciju…")
    status_msg.delete.assert_awaited_once()


async def test_status_message_deleted_when_agent_fails():
    update = _build_update(43)
    status_msg = AsyncMock()
    update.message.reply_text.return_value = status_msg

    with (
        patch(
            "src.telegram_handler.agent_run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("api down"),
        ),
        patch("src.telegram_handler._chat_history") as mock_history,
    ):
        from src.telegram_handler import message_handler
        mock_history.__getitem__ = MagicMock(return_value=[])

        await message_handler(update, MagicMock())

    status_msg.delete.assert_awaited_once()


async def test_handler_survives_status_send_failure():
    """If the initial status send fails, the answer must still be delivered."""
    update = _build_update(44)
    update.message.reply_text.side_effect = RuntimeError("telegram down")

    with (
        patch(
            "src.telegram_handler.agent_run",
            new_callable=AsyncMock,
            return_value=AgentResponse(text="Odgovor. [CONF:NA]"),
        ),
        patch("src.telegram_handler._send_long", new_callable=AsyncMock) as mock_send,
        patch("src.telegram_handler._chat_history") as mock_history,
    ):
        from src.telegram_handler import message_handler
        mock_history.__getitem__ = MagicMock(return_value=[])

        await message_handler(update, MagicMock())

    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][1] == "Odgovor."
