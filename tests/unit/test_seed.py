"""Tests for deterministic development seed data."""

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from parking_assistant.db.models import (
    Base,
    OpeningHour,
    ParkingFacility,
    ParkingSpace,
    PricingRule,
    SpaceStatus,
    SpaceType,
)
from parking_assistant.db.seed import FACILITY_ID, seed_data


def test_seed_creates_expected_realistic_records() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        summary = seed_data(session)
        session.commit()

        assert summary.facilities == 1
        assert summary.spaces == 24
        assert summary.opening_hours == 7
        assert summary.pricing_rules == 4
        assert session.scalar(
            select(func.count()).select_from(ParkingSpace).where(
                ParkingSpace.space_type == SpaceType.ACCESSIBLE
            )
        ) == 3
        assert session.scalar(
            select(func.count()).select_from(ParkingSpace).where(
                ParkingSpace.status == SpaceStatus.OUT_OF_SERVICE
            )
        ) == 2


def test_seed_is_idempotent_across_transactions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        first = seed_data(session)
        session.commit()
    with Session(engine) as session:
        second = seed_data(session)
        session.commit()

        assert second == first
        assert session.scalar(select(func.count()).select_from(ParkingFacility)) == 1
        assert session.scalar(select(func.count()).select_from(ParkingSpace)) == 24
        assert session.scalar(select(func.count()).select_from(OpeningHour)) == 7
        assert session.scalar(select(func.count()).select_from(PricingRule)) == 4


def test_seeded_relationships_resolve_to_the_facility() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_data(session)
        facility = session.get_one(ParkingFacility, FACILITY_ID)

        assert len(facility.spaces) == 24
        assert len(facility.opening_hours) == 7
        assert len(facility.pricing_rules) == 4

