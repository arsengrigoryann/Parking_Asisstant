"""Focused Stage 4 master graph routing, privacy, and lifecycle tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from parking_assistant.application import AssistantResponse
from parking_assistant.config import Settings
from parking_assistant.db.models import Base, ParkingFacility
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.master import MasterRuntimeContext, build_master_graph
from parking_assistant.mcp.client import MCPRecordingError
from parking_assistant.mcp.recorder import ReservationRecordResult
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationField,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.submission import ReservationSubmissionService
from parking_assistant.routing import DynamicSubtype, IntentRoute

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
SYNTHETIC_NAME = "SyntheticCheckpointName"
SYNTHETIC_PLATE = "PIITEST42"


class UnusedProvider:
    def open(self) -> Any:
        raise AssertionError("the legacy Stage 2 graph must not be started by Stage 4 submission")


class FakeConversation:
    def __init__(
        self,
        responses: list[AssistantResponse],
        coordinator: ApprovalWorkflowCoordinator,
        facility_id: UUID,
    ) -> None:
        self.responses = responses
        self.coordinator = coordinator
        self.facility_id = facility_id
        self.turn = 0

    def handle_message(self, message: str, session_id: str) -> AssistantResponse:
        response = self.responses[min(self.turn, len(self.responses) - 1)]
        self.turn += 1
        return response

    def submit_completed(self, session_id: str, *, thread_id: UUID) -> Any:
        details = ReservationDetails(
            first_name=SYNTHETIC_NAME,
            last_name="Driver",
            car_number=SYNTHETIC_PLATE,
            start_datetime=NOW + timedelta(days=1),
            end_datetime=NOW + timedelta(days=1, hours=2),
        )
        return self.coordinator.submit_completed(
            ReservationTurnResult(
                answer="complete",
                status=ReservationStatus.COMPLETE,
                reservation=details,
            ),
            facility_id=self.facility_id,
            idempotency_key=f"opaque-{session_id}",
            thread_id=thread_id,
        )


class RecordingSpy:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls: list[UUID] = []
        self.fail_once = fail_once

    def record_if_approved(self, reservation_id: UUID) -> ReservationRecordResult:
        self.calls.append(reservation_id)
        if self.fail_once and len(self.calls) == 1:
            raise MCPRecordingError("synthetic outage")
        return ReservationRecordResult(reservation_id=reservation_id, outcome="recorded")


def services() -> tuple[
    ReservationSubmissionService,
    ApprovalWorkflowIdentityService,
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
    settings = Settings(database_url="postgresql://x:x@localhost/x", _env_file=None)
    return (
        ReservationSubmissionService(factory, settings, now=lambda: NOW),
        ApprovalWorkflowIdentityService(factory),
        factory,
        facility_id,
    )


def config(session_id: UUID) -> RunnableConfig:
    return {"configurable": {"thread_id": str(session_id)}}


def test_static_and_dynamic_routes_use_existing_conversation_results() -> None:
    reservations, identities, _, facility_id = services()
    responses = [
        AssistantResponse(answer="Grounded location.", route=IntentRoute.STATIC_INFORMATION),
        AssistantResponse(
            answer="7 spaces available.",
            route=IntentRoute.DYNAMIC_INFORMATION,
            dynamic_subtype=DynamicSubtype.AVAILABILITY,
        ),
    ]
    conversation = FakeConversation(
        responses,
        ApprovalWorkflowCoordinator(reservations, identities, UnusedProvider()),
        facility_id,
    )
    graph = build_master_graph(conversation, reservations, None, InMemorySaver())

    first_id, second_id = uuid4(), uuid4()
    static = graph.invoke(
        {"session_id": str(first_id)},
        config=config(first_id),
        context=MasterRuntimeContext(message="Where is it?"),
    )
    dynamic = graph.invoke(
        {"session_id": str(second_id)},
        config=config(second_id),
        context=MasterRuntimeContext(message="Availability?"),
    )

    assert static["route"] == IntentRoute.STATIC_INFORMATION.value
    assert static["final_message"] == "Grounded location."
    assert dynamic["route"] == IntentRoute.DYNAMIC_INFORMATION.value
    assert dynamic["dynamic_subtype"] == DynamicSubtype.AVAILABILITY.value


def test_approved_interrupt_resume_records_once_and_checkpoints_exclude_pii() -> None:
    reservations, identities, _, facility_id = services()
    complete = AssistantResponse(
        answer=f"{SYNTHETIC_NAME} {SYNTHETIC_PLATE}",
        route=IntentRoute.RESERVATION,
        reservation_status=ReservationStatus.COMPLETE,
    )
    conversation = FakeConversation(
        [complete],
        ApprovalWorkflowCoordinator(reservations, identities, UnusedProvider()),
        facility_id,
    )
    saver = InMemorySaver()
    recorder = RecordingSpy()
    graph = build_master_graph(conversation, reservations, recorder, saver)
    session_id = uuid4()

    graph.invoke(
        {"session_id": str(session_id)},
        config=config(session_id),
        context=MasterRuntimeContext(
            message=f"Book for {SYNTHETIC_NAME}, plate {SYNTHETIC_PLATE}"
        ),
    )
    waiting = graph.get_state(config(session_id))
    serialized = str(list(saver.list(config(session_id))))
    reservation_id = UUID(waiting.values["reservation_id"])

    assert waiting.next
    assert SYNTHETIC_NAME not in serialized
    assert SYNTHETIC_PLATE not in serialized

    reservations.approve(reservation_id, decision_by="human-admin")
    graph.invoke(
        Command(resume={"review_completed": True}),
        config=config(session_id),
        context=MasterRuntimeContext(),
    )
    final = graph.get_state(config(session_id)).values

    assert recorder.calls == [reservation_id]
    assert final["status"] == "APPROVED"
    assert final["recording_status"] == "recorded"


def test_rejection_never_records_and_mcp_failure_is_retryable_after_restart() -> None:
    reservations, identities, _, facility_id = services()
    complete = AssistantResponse(
        answer="sensitive response",
        route=IntentRoute.RESERVATION,
        reservation_status=ReservationStatus.COMPLETE,
    )
    coordinator = ApprovalWorkflowCoordinator(reservations, identities, UnusedProvider())

    reject_conversation = FakeConversation([complete], coordinator, facility_id)
    reject_recorder = RecordingSpy()
    rejected_graph = build_master_graph(
        reject_conversation,
        reservations,
        reject_recorder,
        InMemorySaver(),
    )
    rejected_session = uuid4()
    rejected_graph.invoke(
        {"session_id": str(rejected_session)},
        config=config(rejected_session),
        context=MasterRuntimeContext(message="synthetic reservation"),
    )
    rejected_id = UUID(rejected_graph.get_state(config(rejected_session)).values["reservation_id"])
    reservations.reject(rejected_id, decision_by="human-admin", reason="Synthetic")
    rejected_graph.invoke(
        Command(resume=True),
        config=config(rejected_session),
        context=MasterRuntimeContext(),
    )
    assert reject_recorder.calls == []

    saver = InMemorySaver()
    flaky = RecordingSpy(fail_once=True)
    approved_session = uuid4()
    first_graph = build_master_graph(
        FakeConversation([complete], coordinator, facility_id),
        reservations,
        flaky,
        saver,
    )
    first_graph.invoke(
        {"session_id": str(approved_session)},
        config=config(approved_session),
        context=MasterRuntimeContext(message="synthetic reservation"),
    )
    approved_id = UUID(first_graph.get_state(config(approved_session)).values["reservation_id"])
    reservations.approve(approved_id, decision_by="human-admin")
    first_graph.invoke(
        Command(resume=True),
        config=config(approved_session),
        context=MasterRuntimeContext(),
    )
    assert first_graph.get_state(config(approved_session)).values["recording_status"] == (
        "retryable_failure"
    )

    restarted_graph = build_master_graph(
        FakeConversation([complete], coordinator, facility_id),
        reservations,
        flaky,
        saver,
    )
    restarted_graph.invoke(
        {
            "session_id": str(approved_session),
            "reservation_id": str(approved_id),
        },
        config=config(approved_session),
        context=MasterRuntimeContext(),
    )
    assert restarted_graph.get_state(config(approved_session)).values[
        "recording_status"
    ] == "recorded"
    assert flaky.calls == [approved_id, approved_id]


def test_multi_turn_reservation_stays_in_collection_until_complete() -> None:
    reservations, identities, _, facility_id = services()
    conversation = FakeConversation(
        [
            AssistantResponse(
                answer="What is your car number?",
                route=IntentRoute.RESERVATION,
                reservation_status=ReservationStatus.COLLECTING,
                missing_fields=[ReservationField.CAR_NUMBER],
            ),
            AssistantResponse(
                answer="complete sensitive summary",
                route=IntentRoute.RESERVATION,
                reservation_status=ReservationStatus.COMPLETE,
            ),
        ],
        ApprovalWorkflowCoordinator(reservations, identities, UnusedProvider()),
        facility_id,
    )
    graph = build_master_graph(conversation, reservations, None, InMemorySaver())
    session_id = uuid4()

    first = graph.invoke(
        {"session_id": str(session_id)},
        config=config(session_id),
        context=MasterRuntimeContext(message="start"),
    )
    second = graph.invoke(
        {"session_id": str(session_id)},
        config=config(session_id),
        context=MasterRuntimeContext(message=SYNTHETIC_PLATE),
    )

    assert first["reservation_collection_status"] == "collecting"
    assert first["missing_fields"] == ["car_number"]
    assert second["reservation_collection_status"] == "complete"
    assert graph.get_state(config(session_id)).next
