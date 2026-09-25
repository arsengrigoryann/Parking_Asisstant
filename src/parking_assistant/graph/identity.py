"""Transactional workflow-identity service with idempotent creation."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from parking_assistant.db.models import ApprovalWorkflow
from parking_assistant.graph.repository import ApprovalWorkflowRepository


class WorkflowIdentityNotFoundError(LookupError):
    """Raised when no durable workflow mapping exists."""


class ApprovalWorkflowIdentityService:
    """Create and retrieve opaque reservation-to-thread mappings."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        repository: ApprovalWorkflowRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repository = repository or ApprovalWorkflowRepository()

    def get_or_create(
        self,
        reservation_id: UUID,
        *,
        thread_id: UUID | None = None,
    ) -> ApprovalWorkflow:
        """Return the mapping, optionally binding a caller-owned safe graph thread."""
        session = self._session_factory()
        try:
            existing = self._repository.get_by_reservation(session, reservation_id)
            if existing is not None:
                session.expunge(existing)
                return existing
            workflow = ApprovalWorkflow(
                reservation_id=reservation_id,
                **({"thread_id": thread_id} if thread_id is not None else {}),
            )
            self._repository.create(session, workflow)
            session.commit()
            session.expunge(workflow)
            return workflow
        except IntegrityError:
            session.rollback()
            existing = self._repository.get_by_reservation(session, reservation_id)
            if existing is None:
                raise
            session.expunge(existing)
            return existing
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_by_reservation(self, reservation_id: UUID) -> ApprovalWorkflow:
        with self._session_factory() as session:
            workflow = self._repository.get_by_reservation(session, reservation_id)
            if workflow is None:
                raise WorkflowIdentityNotFoundError("approval workflow not found")
            session.expunge(workflow)
            return workflow

    def get_by_thread(self, thread_id: UUID) -> ApprovalWorkflow:
        with self._session_factory() as session:
            workflow = self._repository.get_by_thread(session, thread_id)
            if workflow is None:
                raise WorkflowIdentityNotFoundError("approval workflow not found")
            session.expunge(workflow)
            return workflow
