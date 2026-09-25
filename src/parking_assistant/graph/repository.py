"""PostgreSQL persistence for reservation approval workflow identities."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from parking_assistant.db.models import ApprovalWorkflow


class ApprovalWorkflowRepository:
    """Keep durable reservation/thread mapping SQL focused and testable."""

    def get_by_reservation(
        self, session: Session, reservation_id: UUID
    ) -> ApprovalWorkflow | None:
        return session.scalar(
            select(ApprovalWorkflow).where(
                ApprovalWorkflow.reservation_id == reservation_id
            )
        )

    def get_by_thread(self, session: Session, thread_id: UUID) -> ApprovalWorkflow | None:
        return session.scalar(
            select(ApprovalWorkflow).where(ApprovalWorkflow.thread_id == thread_id)
        )

    def create(self, session: Session, workflow: ApprovalWorkflow) -> None:
        session.add(workflow)
        session.flush()
