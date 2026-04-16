import asyncio
import logging

import cohere
from cohere.errors import TooManyRequestsError

from src.config import COHERE_API_KEY, CONFIG

logger = logging.getLogger(__name__)

DIMENSIONS = CONFIG["retrieval"]["embedding_dimensions"]
_MAX_RETRIES = 5
_BASE_DELAY = 10  # seconds — Cohere trial resets per-minute

_client = cohere.AsyncClientV2(api_key=COHERE_API_KEY)


async def _embed_with_retry(texts: list[str], input_type: str) -> list[list[float]]:
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await _client.embed(
                texts=texts,
                model="embed-v4.0",
                input_type=input_type,
                embedding_types=["float"],
                output_dimension=DIMENSIONS,
            )
            return resp.embeddings.float_
        except TooManyRequestsError:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = _BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Cohere rate limit hit, retrying in %ds (attempt %d/%d)",
                delay, attempt + 1, _MAX_RETRIES,
            )
            await asyncio.sleep(delay)


async def embed_documents(texts: list[str]) -> list[list[float]]:
    vectors = []
    batch_size = 96
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_vectors = await _embed_with_retry(batch, "search_document")
        vectors.extend(batch_vectors)
    return vectors


async def embed_query(text: str) -> list[float]:
    result = await _embed_with_retry([text], "search_query")
    return result[0]
