"""Deterministic, idempotent development data for one parking facility."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from parking_assistant.db.models import (
    OpeningHour,
    ParkingFacility,
    ParkingSpace,
    PricingRule,
    PricingUnit,
    SpaceStatus,
    SpaceType,
    Weekday,
)
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)

FACILITY_ID = uuid5(NAMESPACE_URL, "parking-assistant/facilities/central-station")


@dataclass(frozen=True)
class SeedSummary:
    """Counts after a seed operation."""

    facilities: int
    spaces: int
    opening_hours: int
    pricing_rules: int


@dataclass(frozen=True)
class PricingSeed:
    """Static input used to create a pricing rule."""

    name: str
    space_type: SpaceType | None
    unit: PricingUnit
    duration_minutes: int
    amount: Decimal
    priority: int


def _stable_id(resource: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"parking-assistant/{resource}")


def _space_type(number: int) -> SpaceType:
    if number <= 14:
        return SpaceType.REGULAR
    if number <= 17:
        return SpaceType.ACCESSIBLE
    if number <= 21:
        return SpaceType.EV
    return SpaceType.MOTORCYCLE


def _space_status(number: int) -> SpaceStatus:
    if number in {12, 20}:
        return SpaceStatus.OUT_OF_SERVICE
    if number in {3, 8, 18}:
        return SpaceStatus.OCCUPIED
    return SpaceStatus.AVAILABLE


def seed_data(session: Session) -> SeedSummary:
    """Upsert deterministic development records and return resulting counts."""
    facility = session.get(ParkingFacility, FACILITY_ID)
    if facility is None:
        facility = ParkingFacility(id=FACILITY_ID)
        session.add(facility)
    facility.name = "Central Station Parking"
    facility.address = "15 Railway Square, Yerevan, Armenia"
    facility.timezone = "Asia/Yerevan"
    facility.active = True

    for number in range(1, 25):
        identifier = _stable_id(f"spaces/{number}")
        space = session.get(ParkingSpace, identifier)
        if space is None:
            space = ParkingSpace(id=identifier, facility_id=FACILITY_ID)
            session.add(space)
        space.space_number = f"P-{number:02d}"
        space.space_type = _space_type(number)
        space.status = _space_status(number)
        space.active = True

    for day in Weekday:
        identifier = _stable_id(f"opening-hours/{day.value}")
        opening_hour = session.get(OpeningHour, identifier)
        if opening_hour is None:
            opening_hour = OpeningHour(id=identifier, facility_id=FACILITY_ID)
            session.add(opening_hour)
        opening_hour.weekday = day
        opening_hour.opens_at = (
            time(8, 0) if day in {Weekday.SATURDAY, Weekday.SUNDAY} else time(7, 0)
        )
        opening_hour.closes_at = (
            time(22, 0) if day in {Weekday.SATURDAY, Weekday.SUNDAY} else time(23, 0)
        )
        opening_hour.is_closed = False

    pricing = (
        PricingSeed(
            "regular-hourly", None, PricingUnit.PER_HOUR, 60, Decimal("3.50"), 100
        ),
        PricingSeed(
            "regular-daily-maximum",
            None,
            PricingUnit.DAILY_MAXIMUM,
            1440,
            Decimal("24.00"),
            200,
        ),
        PricingSeed(
            "ev-hourly", SpaceType.EV, PricingUnit.PER_HOUR, 60, Decimal("4.50"), 50
        ),
        PricingSeed(
            "motorcycle-hourly",
            SpaceType.MOTORCYCLE,
            PricingUnit.PER_HOUR,
            60,
            Decimal("2.00"),
            50,
        ),
    )
    for rule_seed in pricing:
        identifier = _stable_id(f"pricing/{rule_seed.name}")
        rule = session.get(PricingRule, identifier)
        if rule is None:
            rule = PricingRule(id=identifier, facility_id=FACILITY_ID)
            session.add(rule)
        rule.name = rule_seed.name
        rule.space_type = rule_seed.space_type
        rule.unit = rule_seed.unit
        rule.duration_minutes = rule_seed.duration_minutes
        rule.amount = rule_seed.amount
        rule.currency = "USD"
        rule.priority = rule_seed.priority
        rule.active = True

    session.flush()
    return SeedSummary(
        facilities=session.scalar(select(func.count()).select_from(ParkingFacility)) or 0,
        spaces=session.scalar(select(func.count()).select_from(ParkingSpace)) or 0,
        opening_hours=session.scalar(select(func.count()).select_from(OpeningHour)) or 0,
        pricing_rules=session.scalar(select(func.count()).select_from(PricingRule)) or 0,
    )


def main() -> None:
    """Seed the configured development database."""
    engine = create_database_engine()
    try:
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            summary = seed_data(session)
        print(
            "Seed complete: "
            f"{summary.facilities} facility, {summary.spaces} spaces, "
            f"{summary.opening_hours} opening-hour rows, "
            f"{summary.pricing_rules} pricing rules"
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
