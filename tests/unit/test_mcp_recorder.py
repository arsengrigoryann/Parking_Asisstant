"""Authorization, formatting, idempotency, and concurrency tests for Stage 3A."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.mcp.recorder import (
    ApprovedReservationRecorder,
    InvalidApprovalError,
    ReservationNotApprovedError,
    serialize_approved_reservation,
)
from parking_assistant.reservations.submission import ReservationNotFoundError

START = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)
DECISION = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)


class Rows:
    def __init__(self, rows: dict[UUID, ReservationRequest]) -> None:
        self.rows = rows

    def get(self, reservation_id: UUID) -> ReservationRequest:
        try:
            return self.rows[reservation_id]
        except KeyError as error:
            raise ReservationNotFoundError("reservation request not found") from error


def row(
    status: ReservationRequestStatus = ReservationRequestStatus.APPROVED,
    *,
    decision_at: datetime | None = DECISION,
) -> ReservationRequest:
    return ReservationRequest(
        id=uuid4(),
        first_name="Test",
        last_name="User",
        car_number="DEMO123",
        start_datetime=START,
        end_datetime=START + timedelta(hours=3),
        facility_id=uuid4(),
        status=status,
        decision_at=decision_at,
        decision_by="synthetic-admin" if decision_at else None,
        idempotency_digest="d" * 64,
    )


def recorder(tmp_path: Path, reservation: ReservationRequest) -> ApprovedReservationRecorder:
    return ApprovedReservationRecorder(
        Rows({reservation.id: reservation}),
        tmp_path / "nested" / "confirmed.txt",
    )


def test_approved_record_has_exact_format_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    reservation = row()
    service = recorder(tmp_path, reservation)

    first = service.record(reservation.id)
    second = service.record(reservation.id)

    assert first.outcome == "recorded"
    assert second.outcome == "already_recorded"
    assert (tmp_path / "nested" / "confirmed.txt").read_text(encoding="utf-8") == (
        "Test User | DEMO123 | "
        "2026-09-27 06:00+00:00\N{EN DASH}2026-09-27 09:00+00:00 | "
        "2026-09-25 07:30:00+00:00\n"
    )


@pytest.mark.parametrize(
    "status",
    [
        ReservationRequestStatus.PENDING_APPROVAL,
        ReservationRequestStatus.REJECTED,
        ReservationRequestStatus.CANCELLED,
    ],
)
def test_non_approved_statuses_are_rejected_without_a_file(
    tmp_path: Path,
    status: ReservationRequestStatus,
) -> None:
    reservation = row(status)

    with pytest.raises(ReservationNotApprovedError, match="not approved"):
        recorder(tmp_path, reservation).record(reservation.id)

    assert not (tmp_path / "nested" / "confirmed.txt").exists()


def test_unknown_and_missing_approval_timestamp_are_rejected(tmp_path: Path) -> None:
    unknown = ApprovedReservationRecorder(Rows({}), tmp_path / "unknown.txt")
    missing_timestamp = row(decision_at=None)

    with pytest.raises(ReservationNotFoundError):
        unknown.record(uuid4())
    with pytest.raises(InvalidApprovalError, match="no decision timestamp"):
        ApprovedReservationRecorder(
            Rows({missing_timestamp.id: missing_timestamp}),
            tmp_path / "missing.txt",
        ).record(missing_timestamp.id)

    assert list(tmp_path.glob("*.txt")) == []


def test_concurrent_duplicate_calls_append_one_line(tmp_path: Path) -> None:
    reservation = row()
    service = recorder(tmp_path, reservation)

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(service.record, [reservation.id] * 16))

    assert sum(result.recorded for result in outcomes) == 1
    lines = (tmp_path / "nested" / "confirmed.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_output_path_is_constructor_configuration_only(tmp_path: Path) -> None:
    reservation = row()
    configured = tmp_path / "configured.txt"
    attacker_path = tmp_path / "attacker.txt"
    service = ApprovedReservationRecorder(Rows({reservation.id: reservation}), configured)

    with pytest.raises(TypeError):
        service.record(reservation.id, output_path=attacker_path)  # type: ignore[call-arg]

    assert not configured.exists()
    assert not attacker_path.exists()


def test_serializer_rejects_delimiters_and_handles_sqlite_naive_utc() -> None:
    missing_timestamp = row(decision_at=None)
    with pytest.raises(InvalidApprovalError, match="no decision timestamp"):
        serialize_approved_reservation(missing_timestamp)

    unsafe = row()
    unsafe.first_name = "Unsafe|Name"
    with pytest.raises(InvalidApprovalError, match="unsafe file delimiters"):
        serialize_approved_reservation(unsafe)

    naive = row()
    naive.start_datetime = naive.start_datetime.replace(tzinfo=None)
    assert "2026-09-27 06:00+00:00" in serialize_approved_reservation(naive)
