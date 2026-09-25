"""Unified Stage 4 local demo API and server-side orchestration boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.api.auth import require_admin_token
from parking_assistant.api.main import create_app
from parking_assistant.application import (
    AssistantService,
    ConversationService,
    LazyStaticAnswerer,
)
from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.queries import DynamicParkingService
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.master_runtime import (
    MasterWorkflowCoordinator,
    PostgresMasterGraphProvider,
)
from parking_assistant.graph.runtime import PostgresApprovalGraphProvider
from parking_assistant.guardrails.privacy import PrivacyService
from parking_assistant.guardrails.security import OutputGuardrail
from parking_assistant.mcp.client import ReservationMCPClient
from parking_assistant.reservations.extraction import ReservationExtractor
from parking_assistant.reservations.service import ReservationCollectionService
from parking_assistant.reservations.submission import ReservationSubmissionService
from parking_assistant.routing import IntentRouter

stage4_bearer = HTTPBearer(auto_error=False)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    message: str = Field(min_length=1, max_length=4000)


class WorkflowView(BaseModel):
    """PII-free workflow contract consumed by the demo UI."""

    model_config = ConfigDict(extra="ignore")

    session_id: str | None = None
    response: str | None = None
    route: str | None = None
    dynamic_subtype: str | None = None
    reservation_collection_status: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    reservation_id: str | None = None
    workflow_id: str | None = None
    status: str | None = None
    recording_status: str | None = None
    final_message: str | None = None
    waiting_for_human: bool = False


class MasterCoordinator(Protocol):
    def handle_message(self, session_id: UUID, message: str) -> dict[str, Any]: ...

    def status(self, reservation_id: UUID) -> dict[str, Any]: ...

    def retry_recording(self, reservation_id: UUID) -> dict[str, Any]: ...


def add_stage4_routes(
    application: FastAPI,
    coordinator: MasterCoordinator,
    settings: Settings,
) -> None:
    """Attach the PII-minimal user contract and authenticated retry endpoint."""
    def authenticate_admin(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(stage4_bearer),
        ] = None,
    ) -> None:
        require_admin_token(credentials, settings.admin_api_token)

    @application.post("/api/chat", response_model=WorkflowView)
    def chat(payload: ChatRequest) -> WorkflowView:
        return WorkflowView.model_validate(
            coordinator.handle_message(payload.session_id, payload.message)
        )

    @application.get(
        "/api/workflows/{reservation_id}/status",
        response_model=WorkflowView,
    )
    def workflow_status(reservation_id: UUID) -> WorkflowView:
        return WorkflowView.model_validate(coordinator.status(reservation_id))

    @application.post(
        "/admin/workflows/{reservation_id}/retry-recording",
        response_model=WorkflowView,
        dependencies=[Depends(authenticate_admin)],
    )
    def retry_recording(reservation_id: UUID) -> WorkflowView:
        return WorkflowView.model_validate(coordinator.retry_recording(reservation_id))


def create_unified_app(settings: Settings | None = None) -> FastAPI:
    """Compose one local API without giving the browser any application secrets."""
    resolved = settings or get_settings()
    engine = create_database_engine(resolved)
    sessions = create_session_factory(engine)
    model = create_chat_model(resolved)
    privacy = PrivacyService(resolved.reservation_car_number_pattern)
    dynamic = DynamicParkingService(sessions)
    assistant = AssistantService(
        IntentRouter(model),
        LazyStaticAnswerer(resolved, model),
        dynamic,
        resolved,
        privacy,
    )
    collection = ReservationCollectionService(
        ReservationExtractor(model),
        resolved,
        dynamic.facility_timezone,
    )
    submissions = ReservationSubmissionService(sessions, resolved)
    identities = ApprovalWorkflowIdentityService(sessions)
    stage2_provider = PostgresApprovalGraphProvider(resolved, submissions)
    stage2 = ApprovalWorkflowCoordinator(submissions, identities, stage2_provider)
    conversation = ConversationService(
        assistant,
        collection,
        OutputGuardrail(privacy),
        stage2,
    )
    recorder = (
        ReservationMCPClient(resolved) if resolved.mcp_server_token is not None else None
    )
    master_provider = PostgresMasterGraphProvider(
        resolved,
        conversation,
        submissions,
        recorder,
    )
    coordinator = MasterWorkflowCoordinator(submissions, identities, master_provider)

    application = create_app(
        settings=resolved,
        service=submissions,
        workflow_coordinator=coordinator,
        approved_recorder=None,
    )
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        master_provider.setup()
        async with original_lifespan(app):
            try:
                yield
            finally:
                engine.dispose()

    application.router.lifespan_context = lifespan
    application.title = "Parking Assistant Stage 4 API"
    add_stage4_routes(application, coordinator, resolved)

    # Exposed for focused contract tests; not part of the HTTP surface.
    application.state.master_coordinator = coordinator
    application.state.stage4_components = {
        "conversation": conversation,
        "reservations": submissions,
        "identities": identities,
        "master_provider": master_provider,
        "recorder": recorder,
    }
    return application


app = create_unified_app()


def _safe_workflow_dict(state: dict[str, Any]) -> dict[str, Any]:
    """Retained as an explicit seam for future response contract versioning."""
    return WorkflowView.model_validate(state).model_dump(mode="json")
