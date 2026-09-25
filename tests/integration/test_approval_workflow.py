"""Real PostgresSaver restart, API decision, admin review, and checkpoint privacy test."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from parking_assistant.admin.review import (
    REVIEW_NOTICE,
    AdminReservationRecord,
    AdminReviewBrief,
    AdminReviewPackage,
)
from parking_assistant.api.main import create_app
from parking_assistant.config import get_settings
from parking_assistant.db.models import ApprovalWorkflow, ReservationRequest
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.runtime import PostgresApprovalGraphProvider
from parking_assistant.graph.workflow import DecisionNotRecordedError
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.submission import ReservationSubmissionService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use configured real services",
    ),
]


def _completed() -> ReservationTurnResult:
    start = datetime.now(UTC) + timedelta(days=2)
    return ReservationTurnResult(
        answer="Synthetic durable workflow request",
        status=ReservationStatus.COMPLETE,
        reservation=ReservationDetails(
            first_name="Restarttest",
            last_name="Driver",
            car_number="RST1234",
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        ),
    )


class DeterministicReviewer:
    def __init__(self, reservations: ReservationSubmissionService) -> None:
        self._reservations = reservations

    def review(self, reservation_id: UUID) -> AdminReviewPackage:
        request = self._reservations.get(reservation_id)
        return AdminReviewPackage(
            reservation=AdminReservationRecord.model_validate(request),
            brief=AdminReviewBrief(
                summary="Synthetic restart test request ready for human review.",
                review_notice=REVIEW_NOTICE,
            ),
        )


def test_postgres_workflow_survives_restart_and_api_decision() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    settings = get_settings().model_copy(
        update={
            "admin_api_token": SecretStr("stage2b-integration-token"),
            "admin_api_identity": "stage2b-human",
            "langsmith_tracing": False,
        }
    )
    engine1 = create_database_engine(settings)
    factory1 = create_session_factory(engine1)
    with session_scope(factory1) as session:
        seed_data(session)
    reservations1 = ReservationSubmissionService(factory1, settings)
    provider1 = PostgresApprovalGraphProvider(settings, reservations1)
    provider1.setup()
    coordinator1 = ApprovalWorkflowCoordinator(
        reservations1,
        ApprovalWorkflowIdentityService(factory1),
        provider1,
    )
    escalation = coordinator1.escalate_completed(
        _completed(),
        facility_id=FACILITY_ID,
        idempotency_key=f"stage2b-integration-{uuid4()}",
    )
    reservation_id = escalation.reservation_id
    thread_id = str(escalation.thread_id)
    with pytest.raises(DecisionNotRecordedError):
        coordinator1.resume_for_reservation(reservation_id)
    engine1.dispose()  # simulate process/context shutdown before human review

    engine2 = create_database_engine(settings)
    factory2 = create_session_factory(engine2)
    reservations2 = ReservationSubmissionService(factory2, settings)
    provider2 = PostgresApprovalGraphProvider(settings, reservations2)
    coordinator2 = ApprovalWorkflowCoordinator(
        reservations2,
        ApprovalWorkflowIdentityService(factory2),
        provider2,
    )
    try:
        with TestClient(
            create_app(
                settings=settings,
                service=reservations2,
                workflow_coordinator=coordinator2,
                admin_review_agent=DeterministicReviewer(reservations2),
            )
        ) as api:
            headers = {"Authorization": "Bearer stage2b-integration-token"}
            assert api.get(
                f"/admin/reservations/{reservation_id}/review"
            ).status_code == 401
            review = api.get(
                f"/admin/reservations/{reservation_id}/review", headers=headers
            )
            assert review.status_code == 200
            assert review.json()["reservation"]["car_number"] == "RST1234"
            decision = api.post(
                f"/admin/reservations/{reservation_id}/approve", headers=headers
            )
            assert decision.status_code == 200
            assert decision.json()["status"] == "APPROVED"
            duplicate = api.post(
                f"/admin/reservations/{reservation_id}/approve", headers=headers
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["status"] == "APPROVED"

        final = coordinator2.resume_for_reservation(reservation_id)
        assert final["status"] == "APPROVED"
        assert final["admin_decision_received"] is True

        with factory2() as session:
            mapping = session.scalar(
                select(ApprovalWorkflow).where(
                    ApprovalWorkflow.reservation_id == reservation_id
                )
            )
            assert mapping is not None and str(mapping.thread_id) == thread_id
            checkpoint_json = session.execute(
                text(
                    "SELECT checkpoint::text, metadata::text FROM checkpoints "
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": thread_id},
            ).all()
            blobs = session.execute(
                text(
                    "SELECT blob FROM checkpoint_blobs WHERE thread_id = :thread_id "
                    "UNION ALL SELECT blob FROM checkpoint_writes WHERE thread_id = :thread_id"
                ),
                {"thread_id": thread_id},
            ).scalars()
            serialized = json.dumps([tuple(row) for row in checkpoint_json]) + b"".join(
                bytes(blob) for blob in blobs if blob is not None
            ).decode("latin1")
            assert "Restarttest" not in serialized
            assert "RST1234" not in serialized
    finally:
        provider2.delete_thread(thread_id)
        with session_scope(factory2) as session:
            session.execute(
                delete(ReservationRequest).where(ReservationRequest.id == reservation_id)
            )
        engine2.dispose()


def test_real_rejection_path_resumes_the_interrupted_graph() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    settings = get_settings().model_copy(
        update={
            "admin_api_token": SecretStr("stage2c-rejection-token"),
            "admin_api_identity": "stage2c-human",
            "langsmith_tracing": False,
        }
    )
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    reservations = ReservationSubmissionService(factory, settings)
    provider = PostgresApprovalGraphProvider(settings, reservations)
    provider.setup()
    coordinator = ApprovalWorkflowCoordinator(
        reservations,
        ApprovalWorkflowIdentityService(factory),
        provider,
    )
    escalation = coordinator.escalate_completed(
        _completed(),
        facility_id=FACILITY_ID,
        idempotency_key=f"stage2c-rejection-{uuid4()}",
    )
    reservation_id = escalation.reservation_id
    thread_id = str(escalation.thread_id)
    try:
        with TestClient(
            create_app(
                settings=settings,
                service=reservations,
                workflow_coordinator=coordinator,
                admin_review_agent=DeterministicReviewer(reservations),
            )
        ) as api:
            response = api.post(
                f"/admin/reservations/{reservation_id}/reject",
                headers={"Authorization": "Bearer stage2c-rejection-token"},
                json={"reason": "Synthetic rejection path"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"
        final = coordinator.resume_for_reservation(reservation_id)
        assert final["status"] == "REJECTED"
        assert final["admin_decision_received"] is True
    finally:
        provider.delete_thread(thread_id)
        with session_scope(factory) as session:
            session.execute(
                delete(ReservationRequest).where(ReservationRequest.id == reservation_id)
            )
        engine.dispose()
