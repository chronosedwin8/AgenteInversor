"""Logging estructurado (JSON por defecto) con structlog.

Ningún secreto debe llegar a un log: además de no loguearlos, un procesador sustituye por
"***" cualquier valor secreto registrado (claves de API, tokens, contraseña de la BD) que
aparezca en cualquier campo, incluidas trazas de excepciones. Lo mismo para el logging estándar
(SDK de Binance, aiohttp).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

REDACTED = "***"
_SECRETS: set[str] = set()


def register_secrets(*values: str | None) -> None:
    """Registra valores que nunca deben aparecer en logs (se ignoran los muy cortos)."""
    for v in values:
        if v and len(v) >= 6:
            _SECRETS.add(v)


def redact(text: str) -> str:
    for s in _SECRETS:
        if s in text:
            text = text.replace(s, REDACTED)
    return text


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return type(v)(_redact_value(x) for x in v)
    return v


def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if not _SECRETS:
        return event_dict
    return {k: _redact_value(v) for k, v in event_dict.items()}


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            record.msg = redact(record.getMessage())
            record.args = ()
        return True


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_processor,  # después de formatear excepciones: también las trazas
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
    for h in logging.getLogger().handlers:
        if not any(isinstance(f, _RedactingFilter) for f in h.filters):
            h.addFilter(_RedactingFilter())


def get_logger(name: str | None = None, **bind: Any) -> Any:
    logger = structlog.get_logger(name)
    return logger.bind(**bind) if bind else logger
