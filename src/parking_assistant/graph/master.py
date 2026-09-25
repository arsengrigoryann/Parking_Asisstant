"""Stage 4 master graph joining conversation, approval, and MCP recording."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from parking_assistant.db.models import ReservationRequestStatus
from parking_assistant.graph.coordinator import EscalationResult
from parking_assistant.graph.workflow import build_approval_graph
from parking_assistant.mcp.client import (
    ApprovedReservationRecordingClient,
    MCPRecordingError,
)
from parking_assistant.reservations.models import ReservationStatus
from parking_assistant.reservations.submission import ReservationSubmissionService
from parking_assistant.routing import IntentRoute

RecordingStatus = Literal[
    "not_applicable",
    "pending",
    "recorded",
    "already_recorded",
    "retryable_failure",
]


class MasterGraphState(TypedDict, total=False):
    """Durable state deliberately limited to non-PII workflow facts."""

    session_id: str
    route: str
    dynamic_subtype: str
    reservation_collection_status: str
    missing_fields: list[str]
    reservation_id: str
    workflow_id: str
    status: str
    admin_decision_received: bool
    recording_status: RecordingStatus
    final_message: str


@dataclass
class MasterRuntimeContext:
    """Non-durable ingress values and the PII-bearing display response."""

    message: str | None = None
    display_response: str | None = None


class ConversationOrchestrator(Protocol):
    """Narrow Stage 1/2 boundary consumed by the graph."""

    def handle_message(self, message: str, session_id: str) -> Any: ...

    def submit_completed(
        self,
        session_id: str,
        *,
        thread_id: UUID,
    ) -> EscalationResult: ...


def build_master_graph(
    conversation: ConversationOrchestrator,
    reservations: ReservationSubmissionService,
    recorder: ApprovedReservationRecordingClient | None,
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> Any:
    """Compile the unified graph while keeping raw user turns out of state."""

    def route_request(
        state: MasterGraphState,
        runtime: Runtime[MasterRuntimeContext],
    ) -> MasterGraphState:
        message = runtime.context.message
        if message is None:
            if state.get("reservation_id"):
                return {"route": "RESUME"}
            raise ValueError("a non-durable runtime message is required")

        response = conversation.handle_message(message, state["session_id"])
        runtime.context.display_response = response.answer
        update: MasterGraphState = {
            "route": response.route.value,
            "dynamic_subtype": (
                response.dynamic_subtype.value if response.dynamic_subtype else ""
            ),
            "missing_fields": [field.value for field in response.missing_fields],
        }
        if response.route is not IntentRoute.RESERVATION:
            update["final_message"] = response.answer
            update["recording_status"] = "not_applicable"
            return update

        collection_status = response.reservation_status or ReservationStatus.COLLECTING
        update["reservation_collection_status"] = collection_status.value
        # Reservation turns can echo validated PII. They are returned through runtime context
        # only; the durable response remains intentionally generic.
        update["final_message"] = (
            "Reservation details are complete and ready for submission."
            if collection_status is ReservationStatus.COMPLETE
            else "Additional reservation details are required."
        )
        return update

    def static_information(state: MasterGraphState) -> MasterGraphState:
        return {"final_message": state["final_message"]}

    def dynamic_information(state: MasterGraphState) -> MasterGraphState:
        return {"final_message": state["final_message"]}

    def unsupported(state: MasterGraphState) -> MasterGraphState:
        return {"final_message": state["final_message"]}

    def reservation_flow(state: MasterGraphState) -> MasterGraphState:
        return {"reservation_collection_status": state["reservation_collection_status"]}

    def submit_for_approval(state: MasterGraphState) -> MasterGraphState:
        thread_id = UUID(state["session_id"])
        escalation = conversation.submit_completed(
            state["session_id"],
            thread_id=thread_id,
        )
        return {
            "reservation_id": str(escalation.reservation_id),
            "workflow_id": str(escalation.workflow_id),
            "status": escalation.status.value,
            "admin_decision_received": False,
            "recording_status": "pending",
            "final_message": "Reservation submitted and waiting for human review.",
        }

    def verify_existing_decision(state: MasterGraphState) -> MasterGraphState:
        request = reservations.get(UUID(state["reservation_id"]))
        return {
            "status": request.status.value,
            "admin_decision_received": (
                request.status is not ReservationRequestStatus.PENDING_APPROVAL
            ),
        }

    def record_approved_reservation(state: MasterGraphState) -> MasterGraphState:
        if recorder is None:
            return {
                "recording_status": "retryable_failure",
                "final_message": (
                    "Reservation approved. Recording is temporarily unavailable and may be "
                    "retried."
                ),
            }
        reservation_id = UUID(state["reservation_id"])
        try:
            result = recorder.record_if_approved(reservation_id)
        except MCPRecordingError:
            return {
                "recording_status": "retryable_failure",
                "final_message": (
                    "Reservation approved. Recording is temporarily unavailable and may be "
                    "retried."
                ),
            }
        return {
            "recording_status": result.outcome,
            "final_message": "Reservation approved and confirmation recorded.",
        }

    def decision_complete(state: MasterGraphState) -> MasterGraphState:
        status = state["status"]
        if status == ReservationRequestStatus.REJECTED.value:
            message = "Reservation request was rejected by an administrator."
        elif status == ReservationRequestStatus.CANCELLED.value:
            message = "Reservation request was cancelled."
        else:
            message = "Reservation is still waiting for human review."
        return {"recording_status": "not_applicable", "final_message": message}

    def route_top_level(state: MasterGraphState) -> str:
        return state["route"]

    def route_reservation(state: MasterGraphState) -> str:
        return state["reservation_collection_status"]

    def route_authoritative_status(state: MasterGraphState) -> str:
        return state["status"]

    approval_subgraph = build_approval_graph(reservations, checkpointer=None)
    builder = StateGraph(MasterGraphState, context_schema=MasterRuntimeContext)
    builder.add_node("route_request", route_request)
    builder.add_node("static_information", static_information)
    builder.add_node("dynamic_information", dynamic_information)
    builder.add_node("unsupported", unsupported)
    builder.add_node("reservation_flow", reservation_flow)
    builder.add_node("submit_for_approval", submit_for_approval)
    builder.add_node("approval_workflow", approval_subgraph)
    builder.add_node("verify_decision", verify_existing_decision)
    builder.add_node("record_approved_reservation", record_approved_reservation)
    builder.add_node("decision_complete", decision_complete)

    builder.add_edge(START, "route_request")
    builder.add_conditional_edges(
        "route_request",
        route_top_level,
        {
            IntentRoute.STATIC_INFORMATION.value: "static_information",
            IntentRoute.DYNAMIC_INFORMATION.value: "dynamic_information",
            IntentRoute.RESERVATION.value: "reservation_flow",
            IntentRoute.UNSUPPORTED.value: "unsupported",
            "RESUME": "verify_decision",
        },
    )
    builder.add_edge("static_information", END)
    builder.add_edge("dynamic_information", END)
    builder.add_edge("unsupported", END)
    builder.add_conditional_edges(
        "reservation_flow",
        route_reservation,
        {
            ReservationStatus.COLLECTING.value: END,
            ReservationStatus.CANCELLED.value: END,
            ReservationStatus.COMPLETE.value: "submit_for_approval",
        },
    )
    builder.add_edge("submit_for_approval", "approval_workflow")
    builder.add_conditional_edges(
        "approval_workflow",
        route_authoritative_status,
        {
            ReservationRequestStatus.APPROVED.value: "record_approved_reservation",
            ReservationRequestStatus.REJECTED.value: "decision_complete",
            ReservationRequestStatus.CANCELLED.value: "decision_complete",
        },
    )
    builder.add_conditional_edges(
        "verify_decision",
        route_authoritative_status,
        {
            ReservationRequestStatus.APPROVED.value: "record_approved_reservation",
            ReservationRequestStatus.REJECTED.value: "decision_complete",
            ReservationRequestStatus.CANCELLED.value: "decision_complete",
            ReservationRequestStatus.PENDING_APPROVAL.value: "decision_complete",
        },
    )
    builder.add_edge("record_approved_reservation", END)
    builder.add_edge("decision_complete", END)
    return builder.compile(checkpointer=checkpointer, name="parking_master_workflow")
