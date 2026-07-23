"""Structured logging + correlation IDs (M0.6b Phase 1)."""

import json
import logging

from craftsman.core.logging import (
    bind_log_context,
    build_handler,
    clear_log_context,
    configure_logging,
)


def _format_one(record: logging.LogRecord) -> dict:
    """Run a record through the real handler's filter + JSON formatter."""
    handler = build_handler()
    for f in handler.filters:
        f.filter(record)
    return json.loads(handler.formatter.format(record))


def _record(msg="hello") -> logging.LogRecord:
    return logging.LogRecord("craftsman.test", logging.INFO, __file__, 1, msg, None, None)


def test_emits_valid_json_with_renamed_fields():
    clear_log_context()
    out = _format_one(_record("pipeline started"))
    assert out["message"] == "pipeline started"
    assert out["level"] == "INFO"
    assert out["logger"] == "craftsman.test"
    assert "ts" in out


def test_context_ids_are_injected_when_bound():
    clear_log_context()
    bind_log_context(enrollment_id="e-1", lead_id="l-1")
    try:
        out = _format_one(_record())
        assert out["enrollment_id"] == "e-1"
        assert out["lead_id"] == "l-1"
        assert out["message_id"] is None  # unbound → null, not missing
    finally:
        clear_log_context()


def test_context_is_isolated_after_clear():
    bind_log_context(enrollment_id="e-2")
    clear_log_context()
    out = _format_one(_record())
    assert out["enrollment_id"] is None


def test_bind_merges_and_ignores_none():
    clear_log_context()
    bind_log_context(enrollment_id="e-3")
    bind_log_context(lead_id="l-3", message_id=None)  # None is ignored, e-3 preserved
    try:
        out = _format_one(_record())
        assert out["enrollment_id"] == "e-3"
        assert out["lead_id"] == "l-3"
        assert out["message_id"] is None
    finally:
        clear_log_context()


def test_configure_logging_installs_single_handler():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(level="DEBUG")
        assert len(root.handlers) == 1
        assert root.level == logging.DEBUG
        configure_logging(level="INFO")  # idempotent — still one handler
        assert len(root.handlers) == 1
    finally:
        root.handlers, root.level = saved_handlers, saved_level  # don't pollute the suite
