"""Structured JSON logging with correlation IDs.

configure_logging() installs a JSON formatter on the root logger. Task entrypoints
call bind_log_context(...) so every downstream log line carries the lead / enrollment /
message id it belongs to — making a single lead's journey greppable across processes.
"""

import logging
import os
import sys
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

_CONTEXT_FIELDS = ("lead_id", "enrollment_id", "message_id")
_log_context: ContextVar[dict] = ContextVar("craftsman_log_context", default={})


def bind_log_context(**fields) -> None:
    """Merge non-None correlation ids into the current context (per contextvar, so
    concurrent tasks/requests don't bleed into each other)."""
    current = dict(_log_context.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    _log_context.set(current)


def clear_log_context() -> None:
    _log_context.set({})


class CorrelationFilter(logging.Filter):
    """Inject the bound correlation ids onto every record (None when unset)."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _log_context.get()
        for field in _CONTEXT_FIELDS:
            setattr(record, field, ctx.get(field))
        return True


def build_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s " + " ".join(
        f"%({f})s" for f in _CONTEXT_FIELDS
    )
    handler.setFormatter(
        JsonFormatter(
            fmt,
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    )
    return handler


def configure_logging(level: str | None = None) -> None:
    """Point the root logger at a single JSON handler. Idempotent."""
    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    root.handlers = [build_handler()]
    root.setLevel(lvl)
