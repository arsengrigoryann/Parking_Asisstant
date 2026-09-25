"""Stage 1-to-Stage 2 escalation and durable graph resume coordination."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from parking_assistant.db.models import ApprovalWorkflow, ReservationRequestStatus
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.workflow import DecisionNotRecordedError
from parking_assistant.reservations.models import ReservationStatus, ReservationTurnResult
from parking_assistant.reservations.submission import (
    InvalidReservationError,
    ReservationSubmissionService,
)


class WorkflowGraphProvider(Protocol):
    def open(self) -> AbstractContextManager[Any]: ...


class EscalationResult(BaseModel):
    """Safe identifiers returned after submission and graph interruption."""

    model_config = ConfigDict(frozen=True)

    reservation_id: UUID
    workflow_id: UUID
    thread_id: UUID
    status: ReservationRequestStatus


class ApprovalWorkflowCoordinator:
    """Coordinate existing deterministic services without becoming an authority."""

    def __init__(
        self,
        reservations: ReservationSubmissionService,
        identities: ApprovalWorkflowIdentityService,
        graphs: WorkflowGraphProvider,
    ) -> None:
        self._reservations = reservations
        self._identities = identities
        self._graphs = graphs

    def escalate_completed(
        self,
        result: ReservationTurnResult,
        *,
        facility_id: UUID,
        idempotency_key: str,
    ) -> EscalationResult:
        """Submit one complete draft and start/reuse its durable approval workflow."""
        if result.status != ReservationStatus.COMPLETE or result.reservation is None:
            raise InvalidReservationError("only a complete reservation draft can be escalated")
        request = self._reservations.submit_for_approval(
            result.reservation,
            facility_id=facility_id,
            idempotency_key=idempotency_key,
        )
        workflow = self._identities.get_or_create(request.id)
        config = _thread_config(workflow)
        with self._graphs.open() as graph:
            snapshot = graph.get_state(config)
            if not snapshot.values:
                graph.invoke(
                    {
                        "workflow_id": str(workflow.workflow_id),
                        "reservation_id": str(request.id),
                        "status": request.status.value,
                        "admin_decision_received": False,
                        "final_message": "",
                    },
                    config=config,
                )
        return EscalationResult(
            reservation_id=request.id,
            workflow_id=workflow.workflow_id,
            thread_id=workflow.thread_id,
            status=request.status,
        )

    def resume_for_reservation(self, reservation_id: UUID) -> dict[str, Any]:
        """Resume the mapped thread only after PostgreSQL contains a terminal decision."""
        workflow = self._identities.get_by_reservation(reservation_id)
        request = self._reservations.get(reservation_id)
        if request.status == ReservationRequestStatus.PENDING_APPROVAL:
            raise DecisionNotRecordedError(
                "authenticated administrator decision is not recorded"
            )
        config = _thread_config(workflow)
        with self._graphs.open() as graph:
            snapshot = graph.get_state(config)
            if not snapshot.values:
                raise RuntimeError("durable approval checkpoint not found")
            if not snapshot.next:
                return dict(snapshot.values)
            result = graph.invoke(
                Command(resume={"review_completed": True}),
                config=config,
            )
        return dict(result)


def _thread_config(workflow: ApprovalWorkflow) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(workflow.thread_id)}}
