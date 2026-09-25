"""Focused SQLAlchemy persistence for durable reservation requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from parking_assistant.db.models import (
    ParkingFacility,
    ReservationRequest,
    ReservationRequestStatus,
)


class ReservationRepository:
    """Keep reservation SQL out of application and HTTP layers."""

    def get_facility(self, session: Session, facility_id: UUID) -> ParkingFacility | None:
        return session.scalar(
            select(ParkingFacility).where(
                ParkingFacility.id == facility_id,
                ParkingFacility.active.is_(True),
            )
        )

    def create_pending(self, session: Session, request: ReservationRequest) -> None:
        session.add(request)
        session.flush()

    def get_by_id(self, session: Session, reservation_id: UUID) -> ReservationRequest | None:
        return session.get(ReservationRequest, reservation_id)

    def get_by_idempotency_digest(
        self, session: Session, digest: str
    ) -> ReservationRequest | None:
        return session.scalar(
            select(ReservationRequest).where(ReservationRequest.idempotency_digest == digest)
        )

    def list_pending(self, session: Session) -> list[ReservationRequest]:
        return list(
            session.scalars(
                select(ReservationRequest)
                .where(ReservationRequest.status == ReservationRequestStatus.PENDING_APPROVAL)
                .order_by(ReservationRequest.created_at, ReservationRequest.id)
            ).all()
        )

    def transition_pending(
        self,
        session: Session,
        reservation_id: UUID,
        target: ReservationRequestStatus,
        *,
        decision_at: datetime,
        decision_by: str,
        rejection_reason: str | None,
    ) -> bool:
        """Atomically transition exactly one pending row, returning whether it changed."""
        result = cast(
            CursorResult[Any],
            session.execute(
            update(ReservationRequest)
            .where(
                ReservationRequest.id == reservation_id,
                ReservationRequest.status == ReservationRequestStatus.PENDING_APPROVAL,
            )
            .values(
                status=target,
                updated_at=decision_at,
                decision_at=decision_at,
                decision_by=decision_by,
                rejection_reason=rejection_reason,
            )
            ),
        )
        return result.rowcount == 1
