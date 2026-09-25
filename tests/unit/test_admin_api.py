"""Authentication and endpoint tests for the Stage 2A administrator API."""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from parking_assistant.api.main import create_app
from parking_assistant.config import Settings
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.mcp.client import MCPRecordingError
from parking_assistant.mcp.recorder import ReservationRecordResult
from parking_assistant.reservations.submission import (
    ReservationConflictError,
    ReservationNotFoundError,
)

TOKEN = "synthetic-secret-token"


class StubService:
    def __init__(self) -> None:
        now = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
        self.request = ReservationRequest(
            id=uuid4(),
            first_name="Demo",
            last_name="Driver",
            car_number="TEST123",
            start_datetime=now,
            end_datetime=now,
            facility_id=uuid4(),
            status=ReservationRequestStatus.PENDING_APPROVAL,
            created_at=now,
            updated_at=now,
            idempotency_digest="a" * 64,
        )

    def list_pending(self) -> list[ReservationRequest]:
        return (
            [self.request]
            if self.request.status == ReservationRequestStatus.PENDING_APPROVAL
            else []
        )

    def get(self, reservation_id: object) -> ReservationRequest:
        if reservation_id != self.request.id:
            raise ReservationNotFoundError("reservation request not found")
        return self.request

    def approve(self, reservation_id: object, *, decision_by: str) -> ReservationRequest:
        self.get(reservation_id)
        if self.request.status == ReservationRequestStatus.REJECTED:
            raise ReservationConflictError("cannot transition REJECTED to APPROVED")
        self.request.status = ReservationRequestStatus.APPROVED
        self.request.decision_by = decision_by
        self.request.decision_at = self.request.updated_at
        return self.request

    def reject(
        self, reservation_id: object, *, decision_by: str, reason: str | None = None
    ) -> ReservationRequest:
        self.get(reservation_id)
        self.request.status = ReservationRequestStatus.REJECTED
        self.request.decision_by = decision_by
        self.request.decision_at = self.request.updated_at
        self.request.rejection_reason = reason
        return self.request

    def cancel(self, reservation_id: object, *, decision_by: str) -> ReservationRequest:
        self.get(reservation_id)
        self.request.status = ReservationRequestStatus.CANCELLED
        self.request.decision_by = decision_by
        self.request.decision_at = self.request.updated_at
        return self.request


def client() -> tuple[TestClient, StubService]:
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        admin_api_token=TOKEN,
        admin_api_identity="synthetic-admin",
        _env_file=None,
    )
    service = StubService()
    return TestClient(create_app(settings=settings, service=service)), service  # type: ignore[arg-type]


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_all_admin_routes_reject_missing_and_invalid_tokens(
    caplog: object,
) -> None:
    api, service = client()
    caplog.set_level(logging.INFO)  # type: ignore[attr-defined]

    assert api.get("/admin/reservations/pending").status_code == 401
    response = api.get(
        "/admin/reservations/pending",
        headers={"Authorization": f"Bearer {TOKEN}-wrong"},
    )

    assert response.status_code == 401
    assert TOKEN not in response.text
    assert TOKEN not in caplog.text  # type: ignore[attr-defined]
    assert service.request.first_name not in caplog.text  # type: ignore[attr-defined]


def test_valid_token_lists_retrieves_approves_and_rejects() -> None:
    api, service = client()

    listed = api.get("/admin/reservations/pending", headers=auth())
    fetched = api.get(f"/admin/reservations/{service.request.id}", headers=auth())
    approved = api.post(
        f"/admin/reservations/{service.request.id}/approve", headers=auth()
    )

    assert listed.status_code == 200
    assert listed.json()[0]["reservation_id"] == str(service.request.id)
    assert fetched.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    service.request.status = ReservationRequestStatus.PENDING_APPROVAL
    rejected = api.post(
        f"/admin/reservations/{service.request.id}/reject",
        headers=auth(),
        json={"reason": "Synthetic reason"},
    )
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["rejection_reason"] == "Synthetic reason"


def test_unknown_and_conflicting_decisions_return_safe_errors() -> None:
    api, service = client()

    missing = api.get(f"/admin/reservations/{uuid4()}", headers=auth())
    api.post(
        f"/admin/reservations/{service.request.id}/reject", headers=auth(), json={}
    )
    conflict = api.post(
        f"/admin/reservations/{service.request.id}/approve", headers=auth()
    )

    assert missing.status_code == 404
    assert conflict.status_code == 409
    assert TOKEN not in conflict.text


def test_temporary_mcp_failure_retries_after_committed_approval() -> None:
    settings = Settings(
        database_url="postgresql://test:test@localhost/test",
        admin_api_token=TOKEN,
        admin_api_identity="synthetic-admin",
        _env_file=None,
    )
    service = StubService()

    class FlakyRecorder:
        calls = 0

        def record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult:
            self.calls += 1
            if self.calls == 1:
                raise MCPRecordingError("synthetic outage")
            return ReservationRecordResult(
                reservation_id=reservation_id,
                outcome="recorded",
            )

    recorder = FlakyRecorder()
    api = TestClient(
        create_app(
            settings=settings,
            service=service,  # type: ignore[arg-type]
            approved_recorder=recorder,
        ),
        raise_server_exceptions=False,
    )
    path = f"/admin/reservations/{service.request.id}/approve"

    first = api.post(path, headers=auth())
    second = api.post(path, headers=auth())

    assert first.status_code == 503
    assert first.json()["detail"] == (
        "reservation approved; confirmation recording must be retried"
    )
    assert service.request.status is ReservationRequestStatus.APPROVED
    assert second.status_code == 200
    assert recorder.calls == 2
