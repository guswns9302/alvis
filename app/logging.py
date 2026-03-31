from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging() -> None:
    level_name = os.getenv("ALVIS_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
