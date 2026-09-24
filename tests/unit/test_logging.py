"""Tests for process logging configuration."""

import logging

from parking_assistant.logging import configure_logging


def test_configure_logging_sets_requested_root_level() -> None:
    configure_logging("WARNING")

    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_is_safe_to_call_more_than_once() -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1

