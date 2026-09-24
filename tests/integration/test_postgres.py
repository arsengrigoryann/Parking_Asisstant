"""Real PostgreSQL migration and seed integration coverage."""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from parking_assistant.db.models import ParkingFacility, ParkingSpace
from parking_assistant.db.seed import seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use local services",
    ),
]


def test_migration_seed_and_query_real_postgres() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine()
    try:
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            first = seed_data(session)
        with session_scope(factory) as session:
            second = seed_data(session)
            facility = session.scalar(select(ParkingFacility))
            space_count = session.scalar(select(func.count()).select_from(ParkingSpace))

        assert first == second
        assert facility is not None
        assert facility.name == "Central Station Parking"
        assert space_count == 24
    finally:
        engine.dispose()

