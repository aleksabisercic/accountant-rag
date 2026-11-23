"""agent.effort config is forwarded to the API as output_config.effort."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _end_turn_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Odgovor. [CONF:HIGH]", citations=None)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


async def _run_with_effort(effort):
    from src.agent import run

    api = AsyncMock(return_value=_end_turn_response())
    with (
        patch("src.agent._api_call_with_retry", api),
        patch("src.agent._load_system_prompt", return_value="system"),
        patch("src.agent.EFFORT", effort),
    ):
        await run([{"role": "user", "content": "pitanje"}])
    return api.await_args.kwargs


async def test_effort_sent_via_extra_body():
    kwargs = await _run_with_effort("medium")
    assert kwargs["extra_body"] == {"output_config": {"effort": "medium"}}
    assert "output_config" not in kwargs  # typed kwarg may not exist in the prod SDK


async def test_no_effort_sends_nothing():
    kwargs = await _run_with_effort(None)
    assert "extra_body" not in kwargs


def test_shipped_config_uses_medium():
    from src.agent import EFFORT

    assert EFFORT == "medium"
