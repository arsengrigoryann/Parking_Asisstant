"""Explicit typed queries for current operational parking facts."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

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
from parking_assistant.db.seed import FACILITY_ID


class SpaceTypeAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    space_type: SpaceType
    total_active: int
    available: int


class AvailabilitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_active: int
    available: int
    by_type: list[SpaceTypeAvailability]


class OpeningHoursResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    weekday: Weekday
    opens_at: time | None
    closes_at: time | None
    is_closed: bool


class PricingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    space_type: SpaceType | None
    unit: PricingUnit
    duration_minutes: int
    amount: Decimal
    currency: str


class DynamicParkingService:
    """Read current parking truth only from parameterized SQLAlchemy statements."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        facility_id: UUID = FACILITY_ID,
    ) -> None:
        self._session_factory = session_factory
        self._facility_id = facility_id

    def availability(self) -> AvailabilitySnapshot:
        """Count active spaces and available active spaces, including type breakdown."""
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    ParkingSpace.space_type,
                    func.count(ParkingSpace.id),
                    func.count(ParkingSpace.id).filter(
                        ParkingSpace.status == SpaceStatus.AVAILABLE
                    ),
                )
                .where(
                    ParkingSpace.facility_id == self._facility_id,
                    ParkingSpace.active.is_(True),
                )
                .group_by(ParkingSpace.space_type)
                .order_by(ParkingSpace.space_type)
            ).all()
        by_type = [
            SpaceTypeAvailability(
                space_type=space_type,
                total_active=int(total),
                available=int(available),
            )
            for space_type, total, available in rows
        ]
        return AvailabilitySnapshot(
            total_active=sum(item.total_active for item in by_type),
            available=sum(item.available for item in by_type),
            by_type=by_type,
        )

    def opening_hours(self, weekday: Weekday) -> OpeningHoursResult | None:
        """Return the stored interval for exactly one weekday."""
        with self._session_factory() as session:
            row = session.scalar(
                select(OpeningHour).where(
                    OpeningHour.facility_id == self._facility_id,
                    OpeningHour.weekday == weekday,
                )
            )
        if row is None:
            return None
        return OpeningHoursResult(
            weekday=row.weekday,
            opens_at=row.opens_at,
            closes_at=row.closes_at,
            is_closed=row.is_closed,
        )

    def pricing(self) -> list[PricingResult]:
        """Return active stored pricing rules in deterministic priority/name order."""
        with self._session_factory() as session:
            rules = session.scalars(
                select(PricingRule)
                .where(
                    PricingRule.facility_id == self._facility_id,
                    PricingRule.active.is_(True),
                )
                .order_by(PricingRule.priority, PricingRule.name)
            ).all()
        return [
            PricingResult(
                name=rule.name,
                space_type=rule.space_type,
                unit=rule.unit,
                duration_minutes=rule.duration_minutes,
                amount=rule.amount,
                currency=rule.currency,
            )
            for rule in rules
        ]

    def facility_timezone(self) -> str:
        """Return the facility's configured IANA timezone for reservation normalization."""
        with self._session_factory() as session:
            timezone = session.scalar(
                select(ParkingFacility.timezone).where(
                    ParkingFacility.id == self._facility_id,
                    ParkingFacility.active.is_(True),
                )
            )
        if timezone is None:
            raise ValueError("active parking facility timezone is unavailable")
        return timezone
