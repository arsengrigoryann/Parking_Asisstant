"""Typed administrator API payloads with an intentionally minimal field surface."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.db.models import ReservationRequestStatus


class ReservationAdminResponse(BaseModel):
    """PII-bearing response available only behind administrator authentication."""

    model_config = ConfigDict(from_attributes=True)

    reservation_id: UUID = Field(validation_alias="id")
    first_name: str
    last_name: str
    car_number: str
    start_datetime: datetime
    end_datetime: datetime
    facility_id: UUID
    status: ReservationRequestStatus
    created_at: datetime
    updated_at: datetime
    decision_at: datetime | None
    decision_by: str | None
    rejection_reason: str | None


class RejectionRequest(BaseModel):
    """Optional bounded explanation for an administrator rejection."""

    reason: str | None = Field(default=None, max_length=500)
