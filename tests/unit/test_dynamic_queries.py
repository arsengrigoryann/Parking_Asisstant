"""Unit tests proving dynamic truth comes from explicit database queries."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from parking_assistant.db.models import Base, ParkingSpace, SpaceStatus, Weekday
from parking_assistant.db.queries import DynamicParkingService
from parking_assistant.db.seed import seed_data
from parking_assistant.db.session import create_session_factory


def dynamic_service() -> tuple[DynamicParkingService, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    seed_data(session)
    session.commit()
    return DynamicParkingService(factory), session


def test_availability_uses_active_status_counts_and_type_breakdown() -> None:
    service, session = dynamic_service()
    try:
        initial = service.availability()
        available_space = session.scalar(
            select(ParkingSpace).where(ParkingSpace.status == SpaceStatus.AVAILABLE)
        )
        assert available_space is not None
        available_space.active = False
        session.commit()
        changed = service.availability()
    finally:
        session.close()

    assert initial.total_active == 24
    assert initial.available == 19
    assert sum(item.total_active for item in initial.by_type) == 24
    assert changed.total_active == 23
    assert changed.available == 18


def test_opening_hours_are_read_from_database() -> None:
    service, session = dynamic_service()
    try:
        monday = service.opening_hours(Weekday.MONDAY)
        sunday = service.opening_hours(Weekday.SUNDAY)
    finally:
        session.close()

    assert monday is not None and monday.opens_at is not None
    assert monday.opens_at.hour == 7
    assert sunday is not None and sunday.opens_at is not None
    assert sunday.opens_at.hour == 8


def test_pricing_returns_only_active_stored_rules() -> None:
    service, session = dynamic_service()
    try:
        rules = service.pricing()
    finally:
        session.close()

    assert len(rules) == 4
    assert {str(rule.amount) for rule in rules} == {"2.00", "3.50", "4.50", "24.00"}
    assert [rule.name for rule in rules][:2] == ["ev-hourly", "motorcycle-hourly"]


def test_facility_timezone_comes_from_database() -> None:
    service, session = dynamic_service()
    try:
        assert service.facility_timezone() == "Asia/Yerevan"
    finally:
        session.close()
