"""Generate contextual prefixes for chunks using Anthropic's contextual embeddings approach.

For each chunk, an LLM reads the full document and produces a short context
(2-3 sentences) explaining what the chunk covers and where it sits in the
document. This context is prepended to the chunk before embedding, so the
embedding captures document-level meaning that a bare chunk would lose.

Uses prompt caching: the full document is sent as a cached system block,
so only the first chunk per document pays full input price.
"""

import asyncio
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY, CONFIG

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

_MODEL = CONFIG.get("contextual_embeddings", {}).get("model", "claude-haiku-4-5-20251001")
_MAX_CONCURRENT = CONFIG.get("contextual_embeddings", {}).get("max_concurrent", 5)

_CONTEXT_PROMPT = (
    "Dat ti je dokument i jedan odeljak (chunk) iz tog dokumenta. "
    "Napiši kratak kontekst (2-3 rečenice) koji objašnjava šta taj odeljak sadrži "
    "i gde se nalazi u dokumentu. Kontekst treba da pomogne u pretrazi — "
    "uključi naziv dokumenta, temu, i ključne pojmove ako postoje. "
    "Odgovori SAMO kontekstom, bez dodatnog teksta."
)


async def contextualize_chunk(
    full_document: str,
    chunk_text: str,
) -> str:
    """Generate a contextual prefix for a single chunk.

    Uses prompt caching on the full document so subsequent chunks
    from the same document hit the cache.
    """
    response = await _client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=[
            {
                "type": "text",
                "text": f"<document>\n{full_document}\n</document>",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"{_CONTEXT_PROMPT}\n\n"
                    f"<chunk>\n{chunk_text}\n</chunk>"
                ),
            },
        ],
    )
    return response.content[0].text


async def contextualize_chunks(
    full_document: str,
    chunks: list[dict],
) -> list[dict]:
    """Add contextual prefixes to all chunks from a single document.

    Each chunk dict gets a new 'contextualized_text' key with the
    format: "{context}\n\n{original_text}"

    The original 'text' key is preserved unchanged.
    """
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _process(chunk: dict) -> dict:
        async with semaphore:
            try:
                context = await contextualize_chunk(full_document, chunk["text"])
                return {
                    **chunk,
                    "contextualized_text": f"{context}\n\n{chunk['text']}",
                }
            except Exception:
                logger.warning(
                    "Failed to contextualize chunk from %s, using raw text",
                    chunk["metadata"].get("filename", "unknown"),
                    exc_info=True,
                )
                return {**chunk, "contextualized_text": chunk["text"]}

    results = await asyncio.gather(*[_process(c) for c in chunks])
    return list(results)
