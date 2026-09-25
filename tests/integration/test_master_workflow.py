"""Real PostgresSaver Stage 4 restart, approval, MCP ownership, and privacy flow."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, text

from parking_assistant.api.main import create_app
from parking_assistant.application import AssistantResponse
from parking_assistant.config import get_settings
from parking_assistant.db.models import ReservationRequest
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.graph.coordinator import EscalationResult
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.master_runtime import (
    MasterWorkflowCoordinator,
    PostgresMasterGraphProvider,
)
from parking_assistant.mcp.recorder import ReservationRecordResult
from parking_assistant.reservations.models import ReservationDetails, ReservationStatus
from parking_assistant.reservations.submission import ReservationSubmissionService
from parking_assistant.routing import IntentRoute

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use configured real services",
    ),
]

PII_NAME = "Stagefourrestart"
PII_PLATE = "S4RST42"


class CompletedConversation:
    def __init__(
        self,
        reservations: ReservationSubmissionService,
        identities: ApprovalWorkflowIdentityService,
    ) -> None:
        self._reservations = reservations
        self._identities = identities

    def handle_message(self, message: str, session_id: str) -> AssistantResponse:
        return AssistantResponse(
            answer=f"{PII_NAME} {PII_PLATE}",
            route=IntentRoute.RESERVATION,
            reservation_status=ReservationStatus.COMPLETE,
        )

    def submit_completed(self, session_id: str, *, thread_id: UUID) -> EscalationResult:
        start = datetime.now(UTC) + timedelta(days=2)
        request = self._reservations.submit_for_approval(
            ReservationDetails(
                first_name=PII_NAME,
                last_name="Driver",
                car_number=PII_PLATE,
                start_datetime=start,
                end_datetime=start + timedelta(hours=2),
            ),
            facility_id=FACILITY_ID,
            idempotency_key=f"stage4-{session_id}",
        )
        workflow = self._identities.get_or_create(request.id, thread_id=thread_id)
        return EscalationResult(
            reservation_id=request.id,
            workflow_id=workflow.workflow_id,
            thread_id=workflow.thread_id,
            status=request.status,
        )


class RecordingSpy:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult:
        self.calls.append(reservation_id)
        return ReservationRecordResult(reservation_id=reservation_id, outcome="recorded")


def test_master_graph_survives_restart_and_owns_post_approval_recording() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    settings = get_settings().model_copy(
        update={
            "admin_api_token": SecretStr("stage4-integration-token"),
            "admin_api_identity": "stage4-human",
            "langsmith_tracing": False,
        }
    )
    engine1 = create_database_engine(settings)
    factory1 = create_session_factory(engine1)
    with session_scope(factory1) as session:
        seed_data(session)
    reservations1 = ReservationSubmissionService(factory1, settings)
    identities1 = ApprovalWorkflowIdentityService(factory1)
    recorder = RecordingSpy()
    provider1 = PostgresMasterGraphProvider(
        settings,
        CompletedConversation(reservations1, identities1),
        reservations1,
        recorder,
    )
    provider1.setup()
    coordinator1 = MasterWorkflowCoordinator(reservations1, identities1, provider1)
    session_id = uuid4()
    waiting = coordinator1.handle_message(
        session_id,
        f"Reserve for {PII_NAME} with {PII_PLATE}",
    )
    reservation_id = UUID(waiting["reservation_id"])
    assert waiting["waiting_for_human"] is True
    engine1.dispose()

    engine2 = create_database_engine(settings)
    factory2 = create_session_factory(engine2)
    reservations2 = ReservationSubmissionService(factory2, settings)
    identities2 = ApprovalWorkflowIdentityService(factory2)
    provider2 = PostgresMasterGraphProvider(
        settings,
        CompletedConversation(reservations2, identities2),
        reservations2,
        recorder,
    )
    coordinator2 = MasterWorkflowCoordinator(reservations2, identities2, provider2)
    try:
        with TestClient(
            create_app(
                settings=settings,
                service=reservations2,
                workflow_coordinator=coordinator2,
                approved_recorder=None,
            )
        ) as api:
            decision = api.post(
                f"/admin/reservations/{reservation_id}/approve",
                headers={"Authorization": "Bearer stage4-integration-token"},
            )
        assert decision.status_code == 200
        final = coordinator2.status(reservation_id)
        assert final["status"] == "APPROVED"
        assert final["recording_status"] == "recorded"
        assert recorder.calls == [reservation_id]

        with factory2() as session:
            checkpoint_rows = session.execute(
                text(
                    "SELECT checkpoint::text, metadata::text FROM checkpoints "
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": str(session_id)},
            ).all()
            blobs = session.execute(
                text(
                    "SELECT blob FROM checkpoint_blobs WHERE thread_id = :thread_id "
                    "UNION ALL SELECT blob FROM checkpoint_writes WHERE thread_id = :thread_id"
                ),
                {"thread_id": str(session_id)},
            ).scalars()
            serialized = json.dumps([tuple(row) for row in checkpoint_rows]) + b"".join(
                bytes(blob) for blob in blobs if blob is not None
            ).decode("latin1")
        assert PII_NAME not in serialized
        assert PII_PLATE not in serialized
    finally:
        provider2.delete_thread(str(session_id))
        with session_scope(factory2) as session:
            session.execute(
                delete(ReservationRequest).where(ReservationRequest.id == reservation_id)
            )
        engine2.dispose()
