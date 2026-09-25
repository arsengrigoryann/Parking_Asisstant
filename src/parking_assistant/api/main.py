"""Authenticated administrator REST API for Stage 2A reservation decisions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine

from parking_assistant.admin.review import (
    AdminReviewAgent,
    AdminReviewer,
    AdminReviewPackage,
)
from parking_assistant.api.auth import require_admin_token
from parking_assistant.api.schemas import RejectionRequest, ReservationAdminResponse
from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator
from parking_assistant.graph.identity import (
    ApprovalWorkflowIdentityService,
    WorkflowIdentityNotFoundError,
)
from parking_assistant.graph.runtime import PostgresApprovalGraphProvider
from parking_assistant.graph.workflow import WorkflowStateError
from parking_assistant.mcp.client import (
    ApprovedReservationRecordingClient,
    MCPRecordingError,
    ReservationMCPClient,
)
from parking_assistant.reservations.submission import (
    InvalidReservationError,
    ReservationConflictError,
    ReservationNotFoundError,
    ReservationSubmissionService,
)

bearer = HTTPBearer(auto_error=False)


class ResumableWorkflow(Protocol):
    def resume_for_reservation(self, reservation_id: UUID) -> dict[str, Any]: ...


def create_app(
    *,
    settings: Settings | None = None,
    service: ReservationSubmissionService | None = None,
    workflow_coordinator: ResumableWorkflow | None = None,
    admin_review_agent: AdminReviewer | None = None,
    approved_recorder: ApprovedReservationRecordingClient | None = None,
) -> FastAPI:
    """Build an injectable API app without opening a database connection at import time."""
    resolved_settings = settings or get_settings()
    owns_service = service is None
    engine: Engine | None = None
    graph_provider: PostgresApprovalGraphProvider | None = None
    if service is None:
        engine = create_database_engine(resolved_settings)
        session_factory = create_session_factory(engine)
        service = ReservationSubmissionService(session_factory, resolved_settings)
        graph_provider = PostgresApprovalGraphProvider(resolved_settings, service)
        workflow_coordinator = ApprovalWorkflowCoordinator(
            service,
            ApprovalWorkflowIdentityService(session_factory),
            graph_provider,
        )
    if (
        approved_recorder is None
        and owns_service
        and resolved_settings.mcp_server_token is not None
    ):
        approved_recorder = ReservationMCPClient(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if graph_provider is not None:
            graph_provider.setup()
        yield
        if engine is not None:
            engine.dispose()

    application = FastAPI(title="Parking Assistant Admin API", lifespan=lifespan)

    def authenticate(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer)
        ] = None,
    ) -> None:
        require_admin_token(credentials, resolved_settings.admin_api_token)

    admin = APIRouter(prefix="/admin", dependencies=[Depends(authenticate)])

    @admin.get("/reservations/pending", response_model=list[ReservationAdminResponse])
    def list_pending() -> list[ReservationAdminResponse]:
        return [ReservationAdminResponse.model_validate(row) for row in service.list_pending()]

    @admin.get(
        "/reservations/{reservation_id}/review",
        response_model=AdminReviewPackage,
    )
    def review_reservation(reservation_id: UUID) -> AdminReviewPackage:
        reviewer = admin_review_agent
        if reviewer is None:
            reviewer = AdminReviewAgent(service, create_chat_model(resolved_settings))
        return reviewer.review(reservation_id)

    @admin.get(
        "/reservations/{reservation_id}", response_model=ReservationAdminResponse
    )
    def get_reservation(reservation_id: UUID) -> ReservationAdminResponse:
        return ReservationAdminResponse.model_validate(service.get(reservation_id))

    @admin.post(
        "/reservations/{reservation_id}/approve",
        response_model=ReservationAdminResponse,
    )
    def approve(reservation_id: UUID) -> ReservationAdminResponse:
        result = service.approve(
            reservation_id, decision_by=resolved_settings.admin_api_identity
        )
        if approved_recorder is not None:
            approved_recorder.record_if_approved(reservation_id)
        _resume_if_mapped(workflow_coordinator, reservation_id)
        return ReservationAdminResponse.model_validate(result)

    @admin.post(
        "/reservations/{reservation_id}/reject",
        response_model=ReservationAdminResponse,
    )
    def reject(
        reservation_id: UUID,
        payload: RejectionRequest | None = None,
    ) -> ReservationAdminResponse:
        result = service.reject(
            reservation_id,
            decision_by=resolved_settings.admin_api_identity,
            reason=payload.reason if payload is not None else None,
        )
        _resume_if_mapped(workflow_coordinator, reservation_id)
        return ReservationAdminResponse.model_validate(result)

    @admin.post(
        "/reservations/{reservation_id}/cancel",
        response_model=ReservationAdminResponse,
    )
    def cancel(reservation_id: UUID) -> ReservationAdminResponse:
        result = service.cancel(
            reservation_id, decision_by=resolved_settings.admin_api_identity
        )
        _resume_if_mapped(workflow_coordinator, reservation_id)
        return ReservationAdminResponse.model_validate(result)

    @application.exception_handler(ReservationNotFoundError)
    async def not_found(_: Request, error: ReservationNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.exception_handler(ReservationConflictError)
    async def conflict(_: Request, error: ReservationConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(InvalidReservationError)
    async def invalid(_: Request, error: InvalidReservationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @application.exception_handler(WorkflowStateError)
    async def workflow_conflict(_: Request, error: WorkflowStateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(MCPRecordingError)
    async def recording_unavailable(_: Request, __: MCPRecordingError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "reservation approved; confirmation recording must be retried"},
        )

    application.include_router(admin)
    return application


app = create_app()


def _resume_if_mapped(
    coordinator: ResumableWorkflow | None,
    reservation_id: UUID,
) -> None:
    """Resume Stage 2B workflows while preserving decisions for legacy Stage 2A rows."""
    if coordinator is None:
        return
    try:
        coordinator.resume_for_reservation(reservation_id)
    except WorkflowIdentityNotFoundError:
        return
