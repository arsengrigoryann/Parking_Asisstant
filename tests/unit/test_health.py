"""Tests for database connectivity checks."""

from unittest.mock import MagicMock

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError

from parking_assistant.db.health import database_is_ready


def test_database_is_ready_for_working_connection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    assert database_is_ready(engine) is True


def test_database_is_not_ready_when_connection_fails() -> None:
    engine = MagicMock(spec=Engine)
    engine.connect.side_effect = OperationalError("connect", {}, RuntimeError("offline"))

    assert database_is_ready(engine) is False

