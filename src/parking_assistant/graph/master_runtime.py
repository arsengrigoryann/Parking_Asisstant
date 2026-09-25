"""Durable PostgreSQL runtime and coordination for the Stage 4 master graph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row

from parking_assistant.config import Settings
from parking_assistant.db.models import ReservationRequestStatus
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.master import (
    ConversationOrchestrator,
    MasterRuntimeContext,
    build_master_graph,
)
from parking_assistant.graph.runtime import _checkpoint_connection_kwargs, _safe_serializer
from parking_assistant.graph.workflow import DecisionNotRecordedError
from parking_assistant.mcp.client import ApprovedReservationRecordingClient
from parking_assistant.reservations.submission import ReservationSubmissionService


class PostgresMasterGraphProvider:
    """Compile the master graph against short-lived PostgresSaver connections."""

    def __init__(
        self,
        settings: Settings,
        conversation: ConversationOrchestrator,
        reservations: ReservationSubmissionService,
        recorder: ApprovedReservationRecordingClient | None,
    ) -> None:
        self._connection_kwargs = _checkpoint_connection_kwargs(settings)
        self._conversation = conversation
        self._reservations = reservations
        self._recorder = recorder

    @contextmanager
    def open(self) -> Iterator[Any]:
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            checkpointer = PostgresSaver(connection, serde=_safe_serializer())
            yield build_master_graph(
                self._conversation,
                self._reservations,
                self._recorder,
                checkpointer,
            )

    def setup(self) -> None:
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            PostgresSaver(connection, serde=_safe_serializer()).setup()

    def delete_thread(self, thread_id: str) -> None:
        """Delete one exact demo/test thread without touching business records."""
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection, connection.cursor() as cursor:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE thread_id = %s",
                    (thread_id,),
                )


class MasterWorkflowCoordinator:
    """Invoke, resume, and retry the master graph using its mapped durable thread."""

    def __init__(
        self,
        reservations: ReservationSubmissionService,
        identities: ApprovalWorkflowIdentityService,
        graphs: PostgresMasterGraphProvider,
    ) -> None:
        self._reservations = reservations
        self._identities = identities
        self._graphs = graphs

    def handle_message(self, session_id: UUID, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("message must not be blank")
        context = MasterRuntimeContext(message=message)
        config = self._config(session_id)
        with self._graphs.open() as graph:
            snapshot = graph.get_state(config)
            if snapshot.next:
                state = dict(snapshot.values)
                state["response"] = state.get(
                    "final_message",
                    "Reservation is waiting for human review.",
                )
                state["waiting_for_human"] = True
                return state
            graph.invoke(
                {"session_id": str(session_id)},
                config=config,
                context=context,
            )
            snapshot = graph.get_state(config)
        state = dict(snapshot.values)
        state["response"] = context.display_response or state.get("final_message", "")
        state["waiting_for_human"] = bool(snapshot.next)
        return state

    def resume_for_reservation(self, reservation_id: UUID) -> dict[str, Any]:
        workflow = self._identities.get_by_reservation(reservation_id)
        request = self._reservations.get(reservation_id)
        if request.status is ReservationRequestStatus.PENDING_APPROVAL:
            raise DecisionNotRecordedError(
                "authenticated administrator decision is not recorded"
            )
        config = self._config(workflow.thread_id)
        with self._graphs.open() as graph:
            snapshot = graph.get_state(config)
            if not snapshot.values:
                raise RuntimeError("durable master checkpoint not found")
            if snapshot.next:
                graph.invoke(
                    Command(resume={"review_completed": True}),
                    config=config,
                    context=MasterRuntimeContext(),
                )
                snapshot = graph.get_state(config)
        return dict(snapshot.values)

    def retry_recording(self, reservation_id: UUID) -> dict[str, Any]:
        workflow = self._identities.get_by_reservation(reservation_id)
        config = self._config(workflow.thread_id)
        with self._graphs.open() as graph:
            graph.invoke(
                {
                    "session_id": str(workflow.thread_id),
                    "reservation_id": str(reservation_id),
                },
                config=config,
                context=MasterRuntimeContext(),
            )
            snapshot = graph.get_state(config)
        return dict(snapshot.values)

    def status(self, reservation_id: UUID) -> dict[str, Any]:
        workflow = self._identities.get_by_reservation(reservation_id)
        config = self._config(workflow.thread_id)
        with self._graphs.open() as graph:
            snapshot = graph.get_state(config)
        if not snapshot.values:
            raise RuntimeError("durable master checkpoint not found")
        result = dict(snapshot.values)
        result["waiting_for_human"] = bool(snapshot.next)
        return result

    @staticmethod
    def _config(thread_id: UUID) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": str(thread_id)}}
