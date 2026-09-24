"""Tests for database session lifecycle behavior."""

import pytest
from sqlalchemy import create_engine, select

from parking_assistant.db.models import Base, ParkingFacility
from parking_assistant.db.session import create_session_factory, session_scope


def test_session_scope_commits_successful_work() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    with session_scope(factory) as session:
        session.add(
            ParkingFacility(
                name="Committed",
                address="1 Test Street",
                timezone="Asia/Yerevan",
            )
        )

    with factory() as session:
        assert session.scalar(select(ParkingFacility.name)) == "Committed"


def test_session_scope_rolls_back_failed_work() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    with pytest.raises(RuntimeError, match="stop"), session_scope(factory) as session:
        session.add(
            ParkingFacility(
                name="Rolled back",
                address="1 Test Street",
                timezone="Asia/Yerevan",
            )
        )
        raise RuntimeError("stop")

    with factory() as session:
        assert session.scalar(select(ParkingFacility)) is None
