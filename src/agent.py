import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import numexpr

from src.config import ANTHROPIC_API_KEY, CONFIG

import cyrtranslit
from src.embeddings import embed_query
from src.vector_store import search

logger = logging.getLogger(__name__)

MODEL = CONFIG["agent"]["model"]
MAX_ROUNDS = CONFIG["agent"]["max_tool_rounds"]
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

CALCULATOR_TOOL = {
    "name": "calculator",
    "description": (
        "Evaluate one or more mathematical expressions and return numeric results. "
        "Use this tool for ANY arithmetic — never attempt mental math. "
        "Input must be valid numeric expressions using Python syntax. "
        "Supported: +, -, *, /, ** (exponent), % (modulo), parentheses, "
        "and functions: sqrt, log (natural), log10, abs, sin, cos, tan, floor. "
        "Constants: pi, e. "
        "IMPORTANT: Use ** for powers, NOT ^. Use only numeric literals, no variable names. "
        "Examples: ['(1500000 * 0.20) + 5000', '(1 + 0.05)**30 * 1000000', 'sqrt(144)']"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expressions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Math expressions, e.g. ['1500000 * 0.20', 'sqrt(144)']",
            }
        },
        "required": ["expressions"],
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
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 0


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


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


_CALC_LOCAL = {"pi": math.pi, "e": math.e}
_MAX_EXPRESSIONS = 10
_MAX_EXPR_LEN = 200


def _handle_calculator(expressions: list[str]) -> str:
    results = []
    for expr in expressions[:_MAX_EXPRESSIONS]:
        if len(expr) > _MAX_EXPR_LEN:
            results.append({"expression": expr[:50] + "...", "error": "Expression too long"})
            continue
        try:
            result = float(numexpr.evaluate(expr, local_dict=_CALC_LOCAL))
            results.append({"expression": expr, "result": result})
        except Exception as e:
            results.append({"expression": expr, "error": str(e)})
    return json.dumps(results)


_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)
_MAX_RETRIES = 3


async def _api_call_with_retry(**kwargs):
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await _client.messages.create(**kwargs)
        except _RETRYABLE as e:
            if attempt == _MAX_RETRIES:
                raise
            wait = 2 ** attempt
            logger.warning("API call failed (attempt %d/%d): %s. Retrying in %ds...", attempt, _MAX_RETRIES, e, wait)
            await asyncio.sleep(wait)


async def run(messages: list[dict]) -> AgentResponse:
    system = _load_system_prompt()
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
        {"type": "custom", **SEARCH_TOOL},
        {"type": "custom", **CALCULATOR_TOOL},
    ]

    sources: list[Source] = []
    seen_sources: set[str] = set()
    web_search_used = False
    total_input = 0
    total_output = 0

    for round_num in range(1, MAX_ROUNDS + 1):
        response = await _api_call_with_retry(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "end_turn":
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
            logger.info(
                "agent rounds=%d input_tokens=%d output_tokens=%d web_search=%s",
                round_num, total_input, total_output, web_search_used,
            )
            return AgentResponse(
                text="\n".join(text_parts),
                sources=sources,
                web_search_used=web_search_used,
                input_tokens=total_input,
                output_tokens=total_output,
                rounds=round_num,
            )

        tool_results = []
        for block in response.content:
            if block.type == "web_search_tool_result":
                web_search_used = True
            elif block.type == "tool_use" and block.name == "calculator":
                result_text = _handle_calculator(block.input["expressions"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
            elif block.type == "tool_use" and block.name == "search_documents":
                result_text = await _handle_search(block.input["query"])
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
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    text_parts = [b.text for b in response.content if b.type == "text"]
    logger.info(
        "agent rounds=%d (max) input_tokens=%d output_tokens=%d web_search=%s",
        MAX_ROUNDS, total_input, total_output, web_search_used,
    )
    return AgentResponse(
        text="\n".join(text_parts) if text_parts else "Nisam uspeo da pronađem odgovor.",
        sources=sources,
        web_search_used=web_search_used,
        input_tokens=total_input,
        output_tokens=total_output,
        rounds=MAX_ROUNDS,
    )
