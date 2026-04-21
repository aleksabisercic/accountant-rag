import asyncio
import logging
import signal
from aiohttp import web

from src.analytics import init_db
from src.qdrant_keepalive import keep_alive_loop
from src.telegram_handler import build_app
from src.config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy health-check access logs
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
# Filter repetitive httpx polling lines, keep everything else
logging.getLogger("httpx").addFilter(
    type("_PollFilter", (), {
        "filter": staticmethod(lambda r: "getUpdates" not in r.getMessage())
    })()
)

HEALTH_PORT = CONFIG.get("health", {}).get("port", 8080)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


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
