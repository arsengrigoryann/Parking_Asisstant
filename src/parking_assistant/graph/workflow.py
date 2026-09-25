"""Focused LangGraph human-in-the-loop reservation approval workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from parking_assistant.db.models import ReservationRequestStatus
from parking_assistant.reservations.submission import ReservationSubmissionService


class ApprovalWorkflowState(TypedDict, total=False):
    """Checkpoint-safe state containing identifiers and workflow outcomes only."""

    workflow_id: str
    reservation_id: str
    status: str
    admin_decision_received: bool
    final_message: str


class WorkflowStateError(RuntimeError):
    """Raised when durable graph state and authoritative reservation state disagree."""


class DecisionNotRecordedError(WorkflowStateError):
    """Raised when a resume is attempted before PostgreSQL records a decision."""


def build_approval_graph(
    reservations: ReservationSubmissionService,
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> CompiledStateGraph[ApprovalWorkflowState, None, ApprovalWorkflowState, ApprovalWorkflowState]:
    """Compile the real Stage 2 workflow with no PII in graph state or interrupts."""

    def load_pending_request(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        request = reservations.get(UUID(state["reservation_id"]))
        if request.status != ReservationRequestStatus.PENDING_APPROVAL:
            raise WorkflowStateError("reservation is not pending approval")
        return {"status": request.status.value, "admin_decision_received": False}

    def prepare_admin_review(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        # The PII-bearing review brief is generated only through the authenticated admin API.
        return {"status": state["status"]}

    def wait_for_human(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        # interrupt() is the first operation. This node performs no pre-interrupt side effects.
        resume_signal = interrupt(
            {
                "reservation_id": state["reservation_id"],
                "workflow_id": state["workflow_id"],
                "action": "administrator_review_required",
            }
        )
        return {"admin_decision_received": resume_signal is not None}

    def verify_decision(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        request = reservations.get(UUID(state["reservation_id"]))
        if request.status == ReservationRequestStatus.PENDING_APPROVAL:
            raise DecisionNotRecordedError(
                "authenticated administrator decision is not recorded"
            )
        if request.status not in {
            ReservationRequestStatus.APPROVED,
            ReservationRequestStatus.REJECTED,
            ReservationRequestStatus.CANCELLED,
        }:
            raise WorkflowStateError("reservation has an unsupported lifecycle status")
        return {
            "status": request.status.value,
            "admin_decision_received": True,
        }

    def route_decision(
        state: ApprovalWorkflowState,
    ) -> Literal["approval_complete", "rejection_complete", "cancelled_complete"]:
        routes: dict[
            str,
            Literal["approval_complete", "rejection_complete", "cancelled_complete"],
        ] = {
            ReservationRequestStatus.APPROVED.value: "approval_complete",
            ReservationRequestStatus.REJECTED.value: "rejection_complete",
            ReservationRequestStatus.CANCELLED.value: "cancelled_complete",
        }
        try:
            return routes[state["status"]]
        except KeyError as error:
            raise WorkflowStateError("authoritative terminal decision is unavailable") from error

    def approval_complete(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        return {
            "status": state["status"],
            "final_message": "Reservation request was approved by an administrator.",
        }

    def rejection_complete(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        return {
            "status": state["status"],
            "final_message": "Reservation request was rejected by an administrator.",
        }

    def cancelled_complete(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        return {
            "status": state["status"],
            "final_message": "Reservation request was cancelled.",
        }

    builder: StateGraph[
        ApprovalWorkflowState,
        None,
        ApprovalWorkflowState,
        ApprovalWorkflowState,
    ] = StateGraph(ApprovalWorkflowState)
    builder.add_node("load_pending_request", load_pending_request)
    builder.add_node("prepare_admin_review", prepare_admin_review)
    builder.add_node("wait_for_human", wait_for_human)
    builder.add_node("verify_decision", verify_decision)
    builder.add_node("approval_complete", approval_complete)
    builder.add_node("rejection_complete", rejection_complete)
    builder.add_node("cancelled_complete", cancelled_complete)
    builder.add_edge(START, "load_pending_request")
    builder.add_edge("load_pending_request", "prepare_admin_review")
    builder.add_edge("prepare_admin_review", "wait_for_human")
    builder.add_edge("wait_for_human", "verify_decision")
    builder.add_conditional_edges("verify_decision", route_decision)
    builder.add_edge("approval_complete", END)
    builder.add_edge("rejection_complete", END)
    builder.add_edge("cancelled_complete", END)
    return builder.compile(checkpointer=checkpointer, name="parking_approval_workflow")
