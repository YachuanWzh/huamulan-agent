import asyncio
import logging
import sys

import uvicorn


def main() -> None:
    # Windows: psycopg (PostgreSQL driver) requires SelectorEventLoop.
    # Without this the server hangs on first DB query.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Stream cache debug logs to stderr so you can see every hit / miss.
    _setup_cache_logging()

    uvicorn.run(
        "personal_assistant.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


def _setup_cache_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    cache_logger = logging.getLogger("personal_assistant.cache")
    cache_logger.addHandler(handler)
    cache_logger.setLevel(logging.DEBUG)
    cache_logger.propagate = False  # don't double-log through the root logger


if __name__ == "__main__":
    main()
