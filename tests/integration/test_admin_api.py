"""Real PostgreSQL migration, lifecycle, and administrator API integration coverage."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import delete

from parking_assistant.api.main import create_app
from parking_assistant.config import Settings
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.reservations.models import ReservationDetails
from parking_assistant.reservations.submission import (
    ReservationConflictError,
    ReservationSubmissionService,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use local services",
    ),
]


def _details(suffix: str) -> ReservationDetails:
    start = datetime.now(UTC) + timedelta(days=2)
    return ReservationDetails(
        first_name="Synthetic",
        last_name="Driver",
        car_number=f"TST{suffix[-5:]}".upper(),
        start_datetime=start,
        end_datetime=start + timedelta(hours=2),
    )


def test_real_postgres_admin_api_and_concurrent_decision() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(
        admin_api_token="integration-only-token",
        admin_api_identity="integration-admin",
    )
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    identifiers: list[UUID] = []
    try:
        with session_scope(factory) as session:
            seed_data(session)
        service = ReservationSubmissionService(factory, settings)
        first = service.submit_for_approval(
            _details("11111"),
            facility_id=FACILITY_ID,
            idempotency_key=f"integration-{uuid4()}",
        )
        identifiers.append(first.id)
        api = TestClient(create_app(settings=settings, service=service))
        headers = {"Authorization": "Bearer integration-only-token"}

        assert api.get("/admin/reservations/pending").status_code == 401
        assert api.get(
            f"/admin/reservations/{first.id}", headers=headers
        ).status_code == 200
        approved = api.post(
            f"/admin/reservations/{first.id}/approve", headers=headers
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"

        second = service.submit_for_approval(
            _details("22222"),
            facility_id=FACILITY_ID,
            idempotency_key=f"integration-{uuid4()}",
        )
        identifiers.append(second.id)

        def decide(target: ReservationRequestStatus) -> str:
            try:
                if target == ReservationRequestStatus.APPROVED:
                    return str(
                        service.approve(second.id, decision_by="race-admin").status.value
                    )
                return str(
                    service.reject(
                        second.id,
                        decision_by="race-admin",
                        reason="Synthetic conflict",
                    ).status.value
                )
            except ReservationConflictError:
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(
                executor.map(
                    decide,
                    [ReservationRequestStatus.APPROVED, ReservationRequestStatus.REJECTED],
                )
            )
        assert "CONFLICT" in outcomes
        assert len(outcomes) == 2
        stored = service.get(second.id)
        assert stored.status in {
            ReservationRequestStatus.APPROVED,
            ReservationRequestStatus.REJECTED,
        }
        assert stored.decision_at is not None
    finally:
        if identifiers:
            with session_scope(factory) as session:
                session.execute(
                    delete(ReservationRequest).where(ReservationRequest.id.in_(identifiers))
                )
        engine.dispose()
