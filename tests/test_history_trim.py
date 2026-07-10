"""Regression: trimmed history must never start with an assistant turn.

The API rejects conversations whose first message is not a user turn. The
handler appends the user message and trims BEFORE calling the agent, so an
odd-length history at trim time used to leave a leading assistant entry —
breaking every subsequent message in the chat until /clear or the sweep.
"""

from src import telegram_handler as th


def test_trim_history_never_starts_with_assistant():
    chat_id = 424242
    th._chat_history.pop(chat_id, None)

    for i in range(th.MAX_HISTORY + 3):
        # Mirror message_handler order: append user → trim → (agent) → append assistant
        th._chat_history[chat_id].append({"role": "user", "content": f"u{i}"})
        th._trim_history(chat_id)
        history = th._chat_history[chat_id]
        assert history[0]["role"] == "user", f"turn {i}: history starts with assistant after user-append trim"
        th._chat_history[chat_id].append({"role": "assistant", "content": f"a{i}"})
        th._trim_history(chat_id)
        assert th._chat_history[chat_id][0]["role"] == "user"

    assert len(th._chat_history[chat_id]) <= th.MAX_HISTORY * 2
    th._chat_history.pop(chat_id, None)


def test_trim_history_preserves_pairing():
    chat_id = 424243
    th._chat_history.pop(chat_id, None)
    for i in range(20):
        th._chat_history[chat_id].append({"role": "user", "content": f"u{i}"})
        th._chat_history[chat_id].append({"role": "assistant", "content": f"a{i}"})
        th._trim_history(chat_id)

    history = th._chat_history[chat_id]
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant"] * (len(history) // 2)
    # Most recent exchange survives
    assert history[-1]["content"] == "a19"
    th._chat_history.pop(chat_id, None)
