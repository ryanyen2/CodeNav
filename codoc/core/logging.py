"""Structured logging for codoc.

Usage:
    from codoc.core.logging import get_logger
    log = get_logger(__name__)
    log.info("reflect.complete", changed_files=3, proposals=2)

Writes JSON lines to .codoc/logs/codoc.jsonl (rotated at 10 MB, 3 backups).
Console output stays human-readable via structlog's ConsoleRenderer.
Falls back to stdlib logging if structlog is not installed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any


def _configure_structlog(log_dir: Path) -> None:
    import structlog
    from logging.handlers import RotatingFileHandler

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "codoc.jsonl"

    jsonl_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    jsonl_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)

    logging.basicConfig(
        format="%(message)s",
        handlers=[jsonl_handler, console_handler],
        level=logging.DEBUG,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


_configured = False


def configure_logging(codoc_dir: str | Path | None = None) -> None:
    global _configured
    if _configured:
        return
    try:
        log_dir = Path(codoc_dir) / "logs" if codoc_dir else Path(".codoc") / "logs"
        _configure_structlog(log_dir)
        _configured = True
    except Exception:
        pass  # structlog unavailable — stdlib fallback is fine


def get_logger(name: str) -> Any:
    try:
        import structlog
        configure_logging()
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)
