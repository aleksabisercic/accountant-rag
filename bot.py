import asyncio
import logging
import signal
import time
from aiohttp import web

from src.analytics import init_db
from src.qdrant_keepalive import keep_alive_loop
from src.telegram_handler import build_app
from src.config import CONFIG
from src.vector_store import _client as qdrant_client
from src.embeddings import _client as cohere_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy health-check access logs
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
# Drop scanner traceback spam (Mirai-style bots hitting :8080 with malformed
# CGI exploits → BadHttpMessage from aiohttp's parser). Real aiohttp errors
# still pass through.
logging.getLogger("aiohttp.server").addFilter(
    type("_ScannerNoiseFilter", (), {
        "filter": staticmethod(lambda r: "BadHttpMessage" not in r.getMessage())
    })()
)
# Filter repetitive httpx polling lines, keep everything else
logging.getLogger("httpx").addFilter(
    type("_PollFilter", (), {
        "filter": staticmethod(lambda r: "getUpdates" not in r.getMessage())
    })()
)

HEALTH_PORT = CONFIG.get("health", {}).get("port", 8080)
_HEALTH_TTL_SECONDS = 60
_HEALTH_PROBE_TIMEOUT = 3
_health_cache: dict = {"ts": 0.0, "body": None, "status_code": 200}


async def _probe(coro) -> tuple[bool, str]:
    try:
        await asyncio.wait_for(coro, timeout=_HEALTH_PROBE_TIMEOUT)
        return True, "ok"
    except Exception as e:
        return False, type(e).__name__


async def health_handler(request: web.Request) -> web.Response:
    now = time.monotonic()
    if _health_cache["body"] and (now - _health_cache["ts"] < _HEALTH_TTL_SECONDS):
        return web.json_response(_health_cache["body"], status=_health_cache["status_code"])

    qdrant_ok, qdrant_info = await _probe(qdrant_client.get_collections())
    cohere_ok, cohere_info = await _probe(cohere_client.models.list(page_size=1))
    all_ok = qdrant_ok and cohere_ok
    body = {
        "status": "ok" if all_ok else "degraded",
        "services": {
            "qdrant": {"ok": qdrant_ok, "info": qdrant_info},
            "cohere": {"ok": cohere_ok, "info": cohere_info},
        },
    }
    status_code = 200 if all_ok else 503
    _health_cache.update({"ts": now, "body": body, "status_code": status_code})
    return web.json_response(body, status=status_code)


async def run_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    logger.info("Health check listening on :%d/health", HEALTH_PORT)
    return runner


def main() -> None:
    app = build_app()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Initialize analytics DB
    loop.run_until_complete(init_db())

    # Start health server and Qdrant keep-alive before bot polling
    health_runner = loop.run_until_complete(run_health_server())
    loop.create_task(keep_alive_loop())

    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, _frame) -> None:
        logger.info("Received signal %s, shutting down gracefully...", signal.Signals(sig).name)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        app.run_polling(close_loop=False)
    except SystemExit:
        pass
    finally:
        logger.info("Cleaning up...")
        loop.run_until_complete(health_runner.cleanup())
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
