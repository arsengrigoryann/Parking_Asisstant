"""Persistence and deterministic lifecycle tests for Stage 2A."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from parking_assistant.config import Settings
from parking_assistant.db.models import (
    Base,
    ParkingFacility,
    ReservationRequest,
    ReservationRequestStatus,
)
from parking_assistant.reservations.models import ReservationDetails
from parking_assistant.reservations.submission import (
    InvalidReservationError,
    ReservationConflictError,
    ReservationSubmissionService,
)

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


@pytest.fixture
def persistence() -> tuple[ReservationSubmissionService, sessionmaker[Session], UUID]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    facility_id = uuid4()
    with factory.begin() as session:
        session.add(
            ParkingFacility(
                id=facility_id,
                name="Synthetic Parking",
                address="1 Test Street",
                timezone="Asia/Yerevan",
                active=True,
            )
        )
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        _env_file=None,
    )
    return ReservationSubmissionService(factory, settings, now=lambda: NOW), factory, facility_id


def details() -> ReservationDetails:
    return ReservationDetails(
        first_name="Demo",
        last_name="Driver",
        car_number="35AB123",
        start_datetime=NOW + timedelta(days=1),
        end_datetime=NOW + timedelta(days=1, hours=2),
    )


def test_complete_submission_creates_pending_uuid_and_timestamps(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, factory, facility_id = persistence

    request = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="session-1"
    )

    assert request.id is not None
    assert request.status == ReservationRequestStatus.PENDING_APPROVAL
    assert request.created_at is not None
    assert request.updated_at is not None
    assert service.get_facility_name(facility_id) == "Synthetic Parking"
    with factory() as session:
        stored = session.get(ReservationRequest, request.id)
        assert stored is not None
        assert stored.first_name == "Demo"
        assert stored.car_number == "35AB123"


def test_invalid_or_incomplete_input_is_not_persisted(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, factory, facility_id = persistence

    with pytest.raises(InvalidReservationError):
        service.submit_for_approval(
            cast(ReservationDetails, object()),
            facility_id=facility_id,
            idempotency_key="invalid",
        )

    with factory() as session:
        assert session.query(ReservationRequest).count() == 0


def test_submission_is_idempotent_and_rejects_key_reuse_for_other_details(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, _, facility_id = persistence
    first = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="same-session"
    )
    repeated = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="same-session"
    )
    changed = details().model_copy(update={"car_number": "99ZZ999"})

    assert repeated.id == first.id
    with pytest.raises(ReservationConflictError, match="different details"):
        service.submit_for_approval(
            changed,
            facility_id=facility_id,
            idempotency_key="same-session",
        )


def test_pending_can_be_approved_idempotently_but_not_rejected_afterward(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, _, facility_id = persistence
    request = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="approve"
    )

    approved = service.approve(request.id, decision_by="admin-test")
    repeated = service.approve(request.id, decision_by="admin-test")

    assert approved.status == ReservationRequestStatus.APPROVED
    assert approved.decision_at is not None
    assert approved.decision_at.replace(tzinfo=UTC) == NOW
    assert approved.decision_by == "admin-test"
    assert repeated.status == ReservationRequestStatus.APPROVED
    with pytest.raises(ReservationConflictError, match="APPROVED to REJECTED"):
        service.reject(request.id, decision_by="admin-test", reason="No capacity")


def test_pending_can_be_rejected_with_reason_but_not_approved_afterward(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, _, facility_id = persistence
    request = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="reject"
    )

    rejected = service.reject(request.id, decision_by="admin-test", reason=" No capacity ")

    assert rejected.status == ReservationRequestStatus.REJECTED
    assert rejected.rejection_reason == "No capacity"
    assert rejected.decision_at is not None
    assert rejected.decision_at.replace(tzinfo=UTC) == NOW
    with pytest.raises(ReservationConflictError, match="REJECTED to APPROVED"):
        service.approve(request.id, decision_by="admin-test")


def test_pending_can_be_cancelled_and_is_removed_from_pending_list(
    persistence: tuple[ReservationSubmissionService, sessionmaker[Session], UUID],
) -> None:
    service, _, facility_id = persistence
    request = service.submit_for_approval(
        details(), facility_id=facility_id, idempotency_key="cancel"
    )

    assert [item.id for item in service.list_pending()] == [request.id]
    cancelled = service.cancel(request.id, decision_by="admin-test")

    assert cancelled.status == ReservationRequestStatus.CANCELLED
    assert service.list_pending() == []
