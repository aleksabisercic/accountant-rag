import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from src.config import ANTHROPIC_API_KEY, CONFIG

import cyrtranslit
from src.embeddings import embed_query
from src.vector_store import search

logger = logging.getLogger(__name__)

MODEL = CONFIG["agent"]["model"]
MAX_ROUNDS = CONFIG["agent"]["max_tool_rounds"]
MAX_TOKENS = CONFIG["agent"].get("max_tokens", 8192)
# low | medium | high | xhigh | max. Unset = API default (high).
EFFORT = CONFIG["agent"].get("effort")
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"

SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the accountant's document database for relevant information. "
        "Use this when the user asks about tax, accounting, regulations, or any "
        "topic that might be covered in the uploaded PDF documents. "
        "The query should be in Latin script Serbian or English."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query in Serbian (Latin) or English",
            }
        },
        "required": ["query"],
    },
}



@dataclass
class Source:
    type: str              # "document" | "web"
    name: str              # filename or page title
    section: str | None = None
    url: str | None = None
    cited_text: str | None = None
    score: float | None = None


@dataclass
class AgentResponse:
    text: str
    sources: list[Source] = field(default_factory=list)
    web_search_used: bool = False
    tools_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 0


def _load_system_prompt() -> str:
    prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    now = datetime.now(ZoneInfo("Europe/Belgrade"))
    date_line = f"\n\nDanašnji datum i vreme: {now.strftime('%d.%m.%Y. %H:%M')} (Europe/Belgrade)"
    return prompt + date_line


async def _handle_search(query: str) -> str:
    query_latin = cyrtranslit.to_latin(query, "sr")
    vector = await embed_query(query_latin)
    results = await search(vector)
    if not results:
        return json.dumps({"results": [], "message": "No relevant documents found."})
    return json.dumps(
        {
            "results": [
                {
                    "text": r["text"],
                    "source": r["filename"],
                    "section": r["section"],
                    "relevance": round(r["score"], 3),
                }
                for r in results
            ]
        },
        ensure_ascii=False,
    )


_client = anthropic.AsyncAnthropic(
    api_key=ANTHROPIC_API_KEY,
    max_retries=5,
)


async def _api_call_with_retry(**kwargs):
    return await _client.messages.create(**kwargs)


async def run(messages: list[dict], on_progress=None) -> AgentResponse:
    """Run the agent loop.

    on_progress: optional async callable (round_num, tools_this_round) invoked
    after each non-terminal round, so a frontend can surface progress. Errors
    in the callback are logged and never interrupt the loop.
    """
    system = _load_system_prompt()
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
        {"type": "custom", **SEARCH_TOOL},
    ]
    # Sent via extra_body rather than the typed output_config kwarg: requirements
    # pin only anthropic>=0.40.0 and the prod image's pip layer is cached, so the
    # installed SDK may predate output_config (unknown kwarg = TypeError on every call).
    extra = {"extra_body": {"output_config": {"effort": EFFORT}}} if EFFORT else {}

    sources: list[Source] = []
    seen_sources: set[str] = set()
    tools_used: set[str] = set()
    web_search_used = False
    total_input = 0
    total_output = 0

    for round_num in range(1, MAX_ROUNDS + 1):
        response = await _api_call_with_retry(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            **extra,
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        # Non-end_turn reasons here are terminal: looping again would resend a
        # history ending on an assistant turn, which the API rejects as a prefill.
        if response.stop_reason in (
            "end_turn", "max_tokens", "refusal",
            "stop_sequence", "model_context_window_exceeded",
        ):
            if response.stop_reason != "end_turn":
                logger.warning("agent stopped early: stop_reason=%s", response.stop_reason)
            text_parts = [b.text for b in response.content if b.type == "text"]
            for block in response.content:
                if block.type == "text" and getattr(block, "citations", None):
                    for cite in block.citations:
                        if cite.type == "web_search_result_location":
                            key = cite.url
                            if key not in seen_sources:
                                seen_sources.add(key)
                                sources.append(Source(
                                    type="web",
                                    name=cite.title or "",
                                    url=cite.url,
                                    cited_text=cite.cited_text,
                                ))
                elif block.type == "web_search_tool_result":
                    web_search_used = True
                    tools_used.add("web_search")
                elif block.type == "server_tool_use":
                    name = block.name
                    if name in ("code_execution", "bash_code_execution", "text_editor_code_execution"):
                        name = "code_execution"
                    tools_used.add(name)
                elif block.type in (
                    "bash_code_execution_tool_result",
                    "text_editor_code_execution_tool_result",
                ):
                    tools_used.add("code_execution")
            logger.info(
                "agent rounds=%d input_tokens=%d output_tokens=%d web_search=%s tools=%s",
                round_num, total_input, total_output, web_search_used,
                ",".join(sorted(tools_used)) or "none",
            )
            return AgentResponse(
                text="\n".join(text_parts) if text_parts else "Nisam uspeo da pronađem odgovor.",
                sources=sources,
                web_search_used=web_search_used,
                tools_used=sorted(tools_used),
                input_tokens=total_input,
                output_tokens=total_output,
                rounds=round_num,
            )

        # Collect client-side tool results (search_documents).
        # Server-side tools (code_execution, web_search) are handled by the API
        # — their results are already in response.content, no tool_result needed.
        tool_results = []
        round_tools: set[str] = set()
        for block in response.content:
            if block.type == "web_search_tool_result":
                web_search_used = True
                tools_used.add("web_search")
                round_tools.add("web_search")
            elif block.type == "server_tool_use":
                # Sub-tools bash_code_execution / text_editor_code_execution
                # normalize to "code_execution" for clean tracking
                name = block.name
                if name in ("code_execution", "bash_code_execution", "text_editor_code_execution"):
                    name = "code_execution"
                tools_used.add(name)
                round_tools.add(name)
                logger.debug("server_tool_use: %s (raw: %s)", name, block.name)
            elif block.type in (
                "bash_code_execution_tool_result",
                "text_editor_code_execution_tool_result",
            ):
                tools_used.add("code_execution")
                round_tools.add("code_execution")
            elif block.type == "tool_use" and block.name == "search_documents":
                tools_used.add("search_documents")
                round_tools.add("search_documents")
                # input can be incomplete if the response was cut off mid-block
                query = block.input.get("query") if isinstance(block.input, dict) else None
                if query:
                    try:
                        result_text = await _handle_search(query)
                    except Exception:
                        # Qdrant/Cohere outage (free-tier hibernation, network)
                        # must not kill the whole reply — surface it as a tool
                        # error so the model can fall back to web search.
                        logger.exception("search_documents failed for query: %s", query)
                        result_text = json.dumps({
                            "results": [],
                            "message": "Document search is temporarily unavailable. "
                                       "Use web search or answer from context.",
                        })
                else:
                    result_text = json.dumps({"results": [], "message": "Empty query."})
                result_data = json.loads(result_text)
                for r in result_data.get("results", []):
                    src = r.get("source", "")
                    if src and src not in seen_sources:
                        seen_sources.add(src)
                        sources.append(Source(
                            type="document",
                            name=src,
                            section=r.get("section"),
                            score=r.get("relevance"),
                        ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        messages.append({"role": "assistant", "content": response.content})

        if on_progress is not None:
            try:
                await on_progress(round_num, sorted(round_tools))
            except Exception:
                logger.warning("on_progress callback failed", exc_info=True)

        # Pending tool_use blocks must be answered first — a pause_turn response
        # can carry one, and leaving it unanswered fails the next API call.
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        # pause_turn with no client tool_use: resend history as-is — the API
        # detects the trailing server_tool_use block and resumes automatically;
        # injecting an extra user message ("Continue.") breaks the resume.

    text_parts = [b.text for b in response.content if b.type == "text"]
    logger.info(
        "agent rounds=%d (max) input_tokens=%d output_tokens=%d web_search=%s tools=%s",
        MAX_ROUNDS, total_input, total_output, web_search_used,
        ",".join(sorted(tools_used)) or "none",
    )
    return AgentResponse(
        text="\n".join(text_parts) if text_parts else "Nisam uspeo da pronađem odgovor.",
        sources=sources,
        web_search_used=web_search_used,
        tools_used=sorted(tools_used),
        input_tokens=total_input,
        output_tokens=total_output,
        rounds=MAX_ROUNDS,
    )
