import asyncio
import logging

from qdrant_client import AsyncQdrantClient

from src.config import QDRANT_API_KEY, QDRANT_URL

logger = logging.getLogger(__name__)

INTERVAL = 6 * 60 * 60  # 6 hours


async def _ping() -> None:
    client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    try:
        cols = await client.get_collections()
        logger.debug("Qdrant keep-alive OK: %d collections", len(cols.collections))
    except Exception:
        logger.warning("Qdrant keep-alive ping failed", exc_info=True)
    finally:
        await client.close()


async def keep_alive_loop() -> None:
    logger.info("Qdrant keep-alive started (interval=%ds)", INTERVAL)
    while True:
        await _ping()
        await asyncio.sleep(INTERVAL)
