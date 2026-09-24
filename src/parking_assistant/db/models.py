"""SQLAlchemy models for current parking operations."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from enum import Enum, StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class SpaceType(StrEnum):
    """Supported physical parking-space types."""

    REGULAR = "regular"
    ACCESSIBLE = "accessible"
    EV = "ev"
    MOTORCYCLE = "motorcycle"


class SpaceStatus(StrEnum):
    """Current operational state of a parking space."""

    AVAILABLE = "available"
    OCCUPIED = "occupied"
    OUT_OF_SERVICE = "out_of_service"


class Weekday(StrEnum):
    """Weekdays used by facility opening hours."""

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class PricingUnit(StrEnum):
    """Simple units supported by current pricing rules."""

    PER_HOUR = "per_hour"
    DAILY_MAXIMUM = "daily_maximum"


def enum_values(enum_class: type[Enum]) -> list[str]:
    """Persist string enum values rather than Python member names."""
    return [str(member.value) for member in enum_class]


class Base(DeclarativeBase):
    """Declarative base for the operational database."""


class ParkingFacility(Base):
    """A physical parking facility."""

    __tablename__ = "parking_facilities"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    spaces: Mapped[list[ParkingSpace]] = relationship(
        back_populates="facility", cascade="all, delete-orphan"
    )
    opening_hours: Mapped[list[OpeningHour]] = relationship(
        back_populates="facility", cascade="all, delete-orphan"
    )
    pricing_rules: Mapped[list[PricingRule]] = relationship(
        back_populates="facility", cascade="all, delete-orphan"
    )


class ParkingSpace(Base):
    """A single operational parking space."""

    __tablename__ = "parking_spaces"
    __table_args__ = (
        UniqueConstraint("facility_id", "space_number", name="uq_space_facility_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    facility_id: Mapped[UUID] = mapped_column(
        ForeignKey("parking_facilities.id", ondelete="CASCADE"), index=True
    )
    space_number: Mapped[str] = mapped_column(String(20))
    space_type: Mapped[SpaceType] = mapped_column(
        SqlEnum(
            SpaceType,
            name="space_type",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=True,
        )
    )
    status: Mapped[SpaceStatus] = mapped_column(
        SqlEnum(
            SpaceStatus,
            name="space_status",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=True,
        )
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    facility: Mapped[ParkingFacility] = relationship(back_populates="spaces")


class OpeningHour(Base):
    """Opening interval for one facility weekday."""

    __tablename__ = "opening_hours"
    __table_args__ = (
        UniqueConstraint("facility_id", "weekday", name="uq_opening_hours_facility_weekday"),
        CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND closes_at IS NOT NULL "
            "AND opens_at < closes_at)",
            name="ck_opening_hours_valid_interval",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    facility_id: Mapped[UUID] = mapped_column(
        ForeignKey("parking_facilities.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[Weekday] = mapped_column(
        SqlEnum(
            Weekday,
            name="weekday",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=True,
        )
    )
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)

    facility: Mapped[ParkingFacility] = relationship(back_populates="opening_hours")


class PricingRule(Base):
    """A current deterministic price for a duration and optional space type."""

    __tablename__ = "pricing_rules"
    __table_args__ = (
        UniqueConstraint("facility_id", "name", name="uq_pricing_rule_facility_name"),
        CheckConstraint("amount >= 0", name="ck_pricing_rule_nonnegative_amount"),
        CheckConstraint("duration_minutes > 0", name="ck_pricing_rule_positive_duration"),
        CheckConstraint("length(currency) = 3", name="ck_pricing_rule_currency_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    facility_id: Mapped[UUID] = mapped_column(
        ForeignKey("parking_facilities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    space_type: Mapped[SpaceType | None] = mapped_column(
        SqlEnum(
            SpaceType,
            name="space_type",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=True,
        )
    )
    unit: Mapped[PricingUnit] = mapped_column(
        SqlEnum(
            PricingUnit,
            name="pricing_unit",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=True,
        )
    )
    duration_minutes: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    facility: Mapped[ParkingFacility] = relationship(back_populates="pricing_rules")
