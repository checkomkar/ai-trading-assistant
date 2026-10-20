"""Structured JSON logging (PRD §0.4).

Every record carries: timestamp, service, event, symbol, severity, message, metadata.

    log = get_logger("market_data")
    log.info("tick stored", event="tick.stored", symbol="NIFTY", count=120)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_RESERVED = {"event", "symbol"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "service": getattr(record, "service", record.name),
            "event": getattr(record, "event", None),
            "symbol": getattr(record, "symbol", None),
            "severity": record.levelname,
            "message": record.getMessage(),
            "metadata": getattr(record, "metadata", {}) or {},
        }
        if record.exc_info:
            payload["metadata"]["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ServiceLogger(logging.LoggerAdapter):
    """Adapter that turns keyword arguments into structured fields."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        std = {k: kwargs.pop(k) for k in ("exc_info", "stack_info", "stacklevel") if k in kwargs}
        extra = {"service": self.extra["service"]}
        for key in _RESERVED:
            if key in kwargs:
                extra[key] = kwargs.pop(key)
        extra["metadata"] = kwargs
        return msg, {"extra": extra, **std}


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("trader")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False
    _configured = True


def get_logger(service: str) -> ServiceLogger:
    return ServiceLogger(logging.getLogger(f"trader.{service}"), {"service": service})
