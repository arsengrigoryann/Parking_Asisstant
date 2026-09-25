"""Escalation, interrupt, routing, and safe-state tests for Stage 2B."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from parking_assistant.config import Settings
from parking_assistant.db.models import ApprovalWorkflow, Base, ParkingFacility, ReservationRequest
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator
from parking_assistant.graph.identity import (
    ApprovalWorkflowIdentityService,
    WorkflowIdentityNotFoundError,
)
from parking_assistant.graph.workflow import (
    DecisionNotRecordedError,
    build_approval_graph,
)
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.submission import (
    InvalidReservationError,
    ReservationSubmissionService,
)

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


class MemoryGraphProvider:
    """Isolated unit-test provider; production always uses PostgresSaver."""

    def __init__(self, reservations: ReservationSubmissionService) -> None:
        self.saver = InMemorySaver()
        self._reservations = reservations

    @contextmanager
    def open(self) -> Iterator[Any]:
        yield build_approval_graph(self._reservations, self.saver)


@pytest.fixture
def workflow() -> tuple[
    ApprovalWorkflowCoordinator,
    ReservationSubmissionService,
    MemoryGraphProvider,
    sessionmaker[Session],
    UUID,
]:
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
    reservations = ReservationSubmissionService(factory, settings, now=lambda: NOW)
    provider = MemoryGraphProvider(reservations)
    coordinator = ApprovalWorkflowCoordinator(
        reservations,
        ApprovalWorkflowIdentityService(factory),
        provider,
    )
    return coordinator, reservations, provider, factory, facility_id


def completed_result() -> ReservationTurnResult:
    return ReservationTurnResult(
        answer="Synthetic complete draft",
        status=ReservationStatus.COMPLETE,
        reservation=ReservationDetails(
            first_name="Demo",
            last_name="Driver",
            car_number="35AB123",
            start_datetime=NOW + timedelta(days=1),
            end_datetime=NOW + timedelta(days=1, hours=2),
        ),
    )


def test_completed_draft_submits_once_persists_thread_and_pauses_safely(
    workflow: tuple[
        ApprovalWorkflowCoordinator,
        ReservationSubmissionService,
        MemoryGraphProvider,
        sessionmaker[Session],
        UUID,
    ],
) -> None:
    coordinator, _, provider, factory, facility_id = workflow

    first = coordinator.escalate_completed(
        completed_result(), facility_id=facility_id, idempotency_key="opaque-session-key"
    )
    repeated = coordinator.escalate_completed(
        completed_result(), facility_id=facility_id, idempotency_key="opaque-session-key"
    )

    assert repeated == first
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReservationRequest)) == 1
        assert session.scalar(select(func.count()).select_from(ApprovalWorkflow)) == 1
        mapping = session.scalar(select(ApprovalWorkflow))
        assert mapping is not None
        assert mapping.thread_id == first.thread_id
    with provider.open() as graph:
        snapshot = graph.get_state(
            {"configurable": {"thread_id": str(first.thread_id)}}
        )
    serialized = str(snapshot)
    assert snapshot.next == ("wait_for_human",)
    assert "administrator_review_required" in serialized
    assert "Demo" not in serialized
    assert "35AB123" not in serialized


def test_incomplete_draft_cannot_submit_or_start_workflow(
    workflow: tuple[
        ApprovalWorkflowCoordinator,
        ReservationSubmissionService,
        MemoryGraphProvider,
        sessionmaker[Session],
        UUID,
    ],
) -> None:
    coordinator, _, _, factory, facility_id = workflow
    incomplete = ReservationTurnResult(
        answer="More details needed", status=ReservationStatus.COLLECTING
    )

    with pytest.raises(InvalidReservationError, match="complete"):
        coordinator.escalate_completed(
            incomplete, facility_id=facility_id, idempotency_key="opaque-key"
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReservationRequest)) == 0
        assert session.scalar(select(func.count()).select_from(ApprovalWorkflow)) == 0


@pytest.mark.parametrize(
    ("decision", "expected", "message"),
    [
        ("approve", "APPROVED", "approved"),
        ("reject", "REJECTED", "rejected"),
        ("cancel", "CANCELLED", "cancelled"),
    ],
)
def test_authoritative_database_decision_routes_terminal_branch(
    workflow: tuple[
        ApprovalWorkflowCoordinator,
        ReservationSubmissionService,
        MemoryGraphProvider,
        sessionmaker[Session],
        UUID,
    ],
    decision: str,
    expected: str,
    message: str,
) -> None:
    coordinator, reservations, _, _, facility_id = workflow
    escalation = coordinator.escalate_completed(
        completed_result(), facility_id=facility_id, idempotency_key=f"opaque-{decision}"
    )
    if decision == "approve":
        reservations.approve(escalation.reservation_id, decision_by="human-admin")
    elif decision == "reject":
        reservations.reject(
            escalation.reservation_id,
            decision_by="human-admin",
            reason="Synthetic reason",
        )
    else:
        reservations.cancel(escalation.reservation_id, decision_by="human-admin")

    final = coordinator.resume_for_reservation(escalation.reservation_id)
    duplicate = coordinator.resume_for_reservation(escalation.reservation_id)

    assert final["status"] == expected
    assert message in final["final_message"].lower()
    assert duplicate == final


def test_resume_signal_cannot_masquerade_as_database_decision(
    workflow: tuple[
        ApprovalWorkflowCoordinator,
        ReservationSubmissionService,
        MemoryGraphProvider,
        sessionmaker[Session],
        UUID,
    ],
) -> None:
    coordinator, _, _, _, facility_id = workflow
    escalation = coordinator.escalate_completed(
        completed_result(), facility_id=facility_id, idempotency_key="opaque-pending"
    )

    with pytest.raises(DecisionNotRecordedError, match="not recorded"):
        coordinator.resume_for_reservation(escalation.reservation_id)


def test_workflow_identity_lookup_by_reservation_and_thread_and_missing(
    workflow: tuple[
        ApprovalWorkflowCoordinator,
        ReservationSubmissionService,
        MemoryGraphProvider,
        sessionmaker[Session],
        UUID,
    ],
) -> None:
    coordinator, _, _, factory, facility_id = workflow
    escalation = coordinator.escalate_completed(
        completed_result(), facility_id=facility_id, idempotency_key="opaque-identity"
    )
    identities = ApprovalWorkflowIdentityService(factory)

    by_reservation = identities.get_by_reservation(escalation.reservation_id)
    by_thread = identities.get_by_thread(escalation.thread_id)

    assert by_reservation.workflow_id == escalation.workflow_id
    assert by_thread.reservation_id == escalation.reservation_id
    with pytest.raises(WorkflowIdentityNotFoundError):
        identities.get_by_reservation(uuid4())
    with pytest.raises(WorkflowIdentityNotFoundError):
        identities.get_by_thread(uuid4())
