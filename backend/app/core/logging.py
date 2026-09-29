"""Structured (JSON) logging with request correlation.

Policy: log identifiers and metadata only. Never log evidence content, request
bodies, query strings, headers carrying credentials, or configuration secrets.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("veritas_request_id", default=None)

# Only these extra attributes are emitted; anything else passed via `extra` is dropped
# so that arbitrary objects cannot leak into logs by accident.
_ALLOWED_EXTRA = frozenset(
    {
        "event",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "error_code",
        "exception_type",
        "case_id",
        "entity_id",
        "action",
        "environment",
        "access_mode",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key in _ALLOWED_EXTRA:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn's access log includes raw query strings; VERITAS emits its own access log.
    logging.getLogger("uvicorn.access").disabled = True
    # Readiness checks inspect the migration state; Alembic logs that at INFO on every call.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
