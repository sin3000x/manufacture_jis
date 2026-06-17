from __future__ import annotations

import logging
import signal
import sys

import uvicorn
from loguru import logger

from manufacture_jis import LOG_ROOT

LOG_FILE = LOG_ROOT / "server.log"


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        LOG_FILE,
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.add(sys.stdout, level="INFO", enqueue=True)

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [_InterceptHandler()]
        std_logger.propagate = False


def main() -> int:
    _configure_logging()

    config = uvicorn.Config(
        "manufacture_jis.web.app:app",
        host="0.0.0.0",
        port=8502,
        log_level="info",
        access_log=True,
        log_config=None,
    )
    server = uvicorn.Server(config)

    def _shutdown(_signo, _frame):
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
