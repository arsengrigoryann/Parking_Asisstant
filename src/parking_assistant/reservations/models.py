"""Typed partial and complete reservation collection models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReservationField(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    CAR_NUMBER = "car_number"
    START_DATETIME = "start_datetime"
    END_DATETIME = "end_datetime"


REQUIRED_FIELDS = tuple(ReservationField)


class ReservationDraft(BaseModel):
    """Validated fields collected so far; missing values remain explicit."""

    model_config = ConfigDict(frozen=True)

    first_name: str | None = None
    last_name: str | None = None
    car_number: str | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None


class ReservationDetails(BaseModel):
    """Complete validated reservation data, not a confirmed booking."""

    model_config = ConfigDict(frozen=True)

    first_name: str
    last_name: str
    car_number: str
    start_datetime: datetime
    end_datetime: datetime


class ReservationExtraction(BaseModel):
    """Untrusted structured values extracted from one user message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    first_name: str | None = None
    last_name: str | None = None
    car_number: str | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    correction_fields: list[ReservationField] = Field(default_factory=list)


class ReservationStatus(StrEnum):
    COLLECTING = "collecting"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class ReservationSessionState(BaseModel):
    """In-memory state for one reservation conversation."""

    model_config = ConfigDict(frozen=True)

    draft: ReservationDraft = Field(default_factory=ReservationDraft)
    missing_fields: list[ReservationField] = Field(default_factory=lambda: list(REQUIRED_FIELDS))
    validation_errors: dict[ReservationField, str] = Field(default_factory=dict)
    complete: bool = False


class ReservationTurnResult(BaseModel):
    """User-facing turn plus internal validated state/result."""

    model_config = ConfigDict(frozen=True)

    answer: str
    status: ReservationStatus
    missing_fields: list[ReservationField] = Field(default_factory=list)
    validation_errors: dict[ReservationField, str] = Field(default_factory=dict)
    reservation: ReservationDetails | None = None
