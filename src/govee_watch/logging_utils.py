from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON for scheduled jobs and service logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the govee_watch root logger with structured JSON output."""
    logger = logging.getLogger("govee_watch")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """Emit a structured log record with extra key-value fields."""
    logger.log(level, message, extra={"structured": fields})
