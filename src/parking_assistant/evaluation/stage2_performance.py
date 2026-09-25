"""Reproducible Stage 2 administrator and durable-workflow latency baseline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from parking_assistant.admin.review import AdminReviewAgent
from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.models import ApprovalWorkflow, ReservationRequest
from parking_assistant.db.seed import FACILITY_ID
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.evaluation.performance import OperationBenchmark, benchmark_operation
from parking_assistant.graph.coordinator import ApprovalWorkflowCoordinator, EscalationResult
from parking_assistant.graph.identity import ApprovalWorkflowIdentityService
from parking_assistant.graph.runtime import PostgresApprovalGraphProvider
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.submission import ReservationSubmissionService

DEFAULT_OUTPUT = Path("evaluation/stage2_performance_report.json")
DEFAULT_MARKDOWN = Path("evaluation/stage2_performance_report.md")


class Stage2PerformanceReport(BaseModel):
    """Machine-readable measurements from configured real Stage 2 dependencies."""

    model_config = ConfigDict(frozen=True)

    requested_samples_per_operation: int
    operations: list[OperationBenchmark]
    notes: list[str] = Field(default_factory=list)


def _completed(suffix: str) -> ReservationTurnResult:
    start = datetime.now(UTC) + timedelta(days=2)
    return ReservationTurnResult(
        answer="Synthetic Stage 2 benchmark request",
        status=ReservationStatus.COMPLETE,
        reservation=ReservationDetails(
            first_name="Benchmark",
            last_name="Driver",
            car_number=f"BM{suffix[-8:]}".upper(),
            start_datetime=start,
            end_datetime=start + timedelta(hours=2),
        ),
    )


def _details(suffix: str) -> ReservationDetails:
    result = _completed(suffix).reservation
    assert result is not None
    return result


def _measure(
    name: str,
    operation: Callable[[], object],
    samples: int,
    *,
    network_dependent: bool = False,
) -> OperationBenchmark:
    return benchmark_operation(
        name,
        operation,
        samples,
        network_dependent=network_dependent,
    )


def run_stage2_performance_baseline(
    settings: Settings,
    samples: int,
) -> Stage2PerformanceReport:  # pragma: no cover - real benchmark CLI
    """Measure real PostgreSQL, PostgresSaver, API-service, and OpenAI boundaries."""
    benchmark_settings = settings.model_copy(update={"langsmith_tracing": False})
    engine = create_database_engine(benchmark_settings)
    factory = create_session_factory(engine)
    reservations = ReservationSubmissionService(factory, benchmark_settings)
    identities = ApprovalWorkflowIdentityService(factory)
    provider = PostgresApprovalGraphProvider(benchmark_settings, reservations)
    provider.setup()
    coordinator = ApprovalWorkflowCoordinator(reservations, identities, provider)
    reviewer = AdminReviewAgent(reservations, create_chat_model(benchmark_settings))
    reservation_ids: list[UUID] = []
    thread_ids: list[str] = []

    def submit() -> ReservationRequest:
        result = reservations.submit_for_approval(
            _details(str(uuid4())),
            facility_id=FACILITY_ID,
            idempotency_key=f"stage2-performance-submit-{uuid4()}",
        )
        reservation_ids.append(result.id)
        return result

    def escalate(prefix: str = "start") -> EscalationResult:
        result = coordinator.escalate_completed(
            _completed(str(uuid4())),
            facility_id=FACILITY_ID,
            idempotency_key=f"stage2-performance-{prefix}-{uuid4()}",
        )
        reservation_ids.append(result.reservation_id)
        thread_ids.append(str(result.thread_id))
        return result

    def prepared_pending() -> UUID:
        return submit().id

    lookup_id = prepared_pending()
    review_id = prepared_pending()
    approve_ids = [prepared_pending() for _ in range(samples)]
    reject_ids = [prepared_pending() for _ in range(samples)]
    resume_ids: list[UUID] = []
    for _ in range(samples):
        escalated = escalate("resume")
        reservations.approve(escalated.reservation_id, decision_by="stage2-benchmark")
        resume_ids.append(escalated.reservation_id)
    start_inputs: list[tuple[ReservationTurnResult, str]] = []
    for _ in range(samples):
        completed = _completed(str(uuid4()))
        key = f"stage2-performance-start-{uuid4()}"
        assert completed.reservation is not None
        pending = reservations.submit_for_approval(
            completed.reservation,
            facility_id=FACILITY_ID,
            idempotency_key=key,
        )
        reservation_ids.append(pending.id)
        start_inputs.append((completed, key))

    def start_prepared_workflow() -> EscalationResult:
        completed, key = start_inputs.pop()
        result = coordinator.escalate_completed(
            completed,
            facility_id=FACILITY_ID,
            idempotency_key=key,
        )
        thread_ids.append(str(result.thread_id))
        return result

    def complete_workflow() -> Any:
        escalated = escalate("complete")
        reservations.approve(escalated.reservation_id, decision_by="stage2-benchmark")
        return coordinator.resume_for_reservation(escalated.reservation_id)

    try:
        operations = [
            _measure("reservation_submission", submit, samples),
            _measure("workflow_start_until_interrupt", start_prepared_workflow, samples),
            _measure("admin_reservation_lookup", lambda: reservations.get(lookup_id), samples),
            _measure(
                "admin_review_generation",
                lambda: reviewer.review(review_id),
                samples,
                network_dependent=True,
            ),
            _measure(
                "approve_lifecycle_mutation",
                lambda: reservations.approve(
                    approve_ids.pop(), decision_by="stage2-benchmark"
                ),
                samples,
            ),
            _measure(
                "reject_lifecycle_mutation",
                lambda: reservations.reject(
                    reject_ids.pop(),
                    decision_by="stage2-benchmark",
                    reason="Synthetic benchmark rejection",
                ),
                samples,
            ),
            _measure(
                "graph_resume_after_decision",
                lambda: coordinator.resume_for_reservation(resume_ids.pop()),
                samples,
            ),
            _measure("complete_approval_workflow", complete_workflow, samples),
        ]
    finally:
        with factory() as session:
            persisted_threads = session.scalars(
                select(ApprovalWorkflow.thread_id).where(
                    ApprovalWorkflow.reservation_id.in_(reservation_ids)
                )
            ).all()
        for thread_id in {*thread_ids, *(str(item) for item in persisted_threads)}:
            provider.delete_thread(thread_id)
        if reservation_ids:
            with session_scope(factory) as session:
                session.execute(
                    delete(ReservationRequest).where(ReservationRequest.id.in_(reservation_ids))
                )
        engine.dispose()

    return Stage2PerformanceReport(
        requested_samples_per_operation=samples,
        operations=operations,
        notes=[
            "Deterministic operations use the configured PostgreSQL and PostgresSaver paths.",
            "Admin review generation calls the configured OpenAI model and is reported separately.",
            "Network and model latency is an observed presentation baseline, not an SLA.",
            "Complete workflow excludes review generation so model latency does not obscure "
            "orchestration.",
            "Synthetic identities are deleted after the benchmark.",
        ],
    )


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(report: Stage2PerformanceReport) -> str:
    """Render a compact, presentation-ready latency table."""
    lines = [
        "# Stage 2 performance baseline",
        "",
        "| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | "
        "External model/network |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for item in report.operations:
        lines.append(
            f"| {item.operation} | {item.sample_count} | {item.failures} | "
            f"{_metric(item.average_ms)} | {_metric(item.p50_ms)} | "
            f"{_metric(item.p95_ms)} | {'yes' if item.network_dependent else 'no'} |"
        )
    lines.extend(["", *(f"- {note}" for note in report.notes), ""])
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - real service benchmark CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = run_stage2_performance_baseline(get_settings(), args.samples)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
