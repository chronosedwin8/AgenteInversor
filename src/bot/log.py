"""Logging estructurado (JSON por defecto) con structlog."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Librerías de terceros (SDK de Binance, aiohttp) por el logging estándar, nivel WARNING.
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format="%(name)s %(message)s")


def get_logger(name: str | None = None, **bind: Any) -> Any:
    logger = structlog.get_logger(name)
    return logger.bind(**bind) if bind else logger
