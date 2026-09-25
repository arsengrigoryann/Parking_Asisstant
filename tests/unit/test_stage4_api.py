"""Stage 4 UI/API orchestration contract tests."""

from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parking_assistant.api.unified import add_stage4_routes
from parking_assistant.config import Settings

TOKEN = "synthetic-stage4-admin-token"


class StubCoordinator:
    def __init__(self) -> None:
        self.reservation_id = uuid4()
        self.retry_calls: list[UUID] = []

    def handle_message(self, session_id: UUID, message: str) -> dict[str, Any]:
        return {
            "session_id": str(session_id),
            "response": "Reservation submitted and waiting for human review.",
            "route": "RESERVATION",
            "reservation_id": str(self.reservation_id),
            "status": "PENDING_APPROVAL",
            "waiting_for_human": True,
            # Domain services may hold PII, but the public schema must discard it.
            "first_name": "SyntheticSecretName",
            "car_number": "SECRET42",
        }

    def status(self, reservation_id: UUID) -> dict[str, Any]:
        assert reservation_id == self.reservation_id
        return {
            "reservation_id": str(reservation_id),
            "status": "APPROVED",
            "recording_status": "retryable_failure",
        }

    def retry_recording(self, reservation_id: UUID) -> dict[str, Any]:
        self.retry_calls.append(reservation_id)
        return {
            "reservation_id": str(reservation_id),
            "status": "APPROVED",
            "recording_status": "recorded",
        }


def client() -> tuple[TestClient, StubCoordinator]:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        admin_api_token=TOKEN,
        _env_file=None,
    )
    app = FastAPI()
    coordinator = StubCoordinator()
    add_stage4_routes(app, coordinator, settings)
    return TestClient(app), coordinator


def test_chat_and_status_contract_exposes_only_safe_workflow_fields() -> None:
    api, coordinator = client()
    session_id = uuid4()

    chat = api.post(
        "/api/chat",
        json={"session_id": str(session_id), "message": "Synthetic reservation"},
    )
    status = api.get(f"/api/workflows/{coordinator.reservation_id}/status")

    assert chat.status_code == 200
    assert chat.json()["waiting_for_human"] is True
    assert "first_name" not in chat.json()
    assert "car_number" not in chat.json()
    assert status.json()["recording_status"] == "retryable_failure"


def test_recording_retry_requires_admin_authentication() -> None:
    api, coordinator = client()
    path = f"/admin/workflows/{coordinator.reservation_id}/retry-recording"

    unauthorized = api.post(path)
    authorized = api.post(path, headers={"Authorization": f"Bearer {TOKEN}"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["recording_status"] == "recorded"
    assert coordinator.retry_calls == [coordinator.reservation_id]
