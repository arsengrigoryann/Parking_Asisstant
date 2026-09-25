"""Reproducible Stage 3 PostgreSQL, file, and MCP latency baseline."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete

from parking_assistant.config import Settings, get_settings
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.evaluation.performance import OperationBenchmark, benchmark_operation
from parking_assistant.evaluation.stage3_support import (
    free_local_port,
    running_server,
    synthetic_details,
)
from parking_assistant.mcp.client import ReservationMCPClient
from parking_assistant.mcp.recorder import ApprovedReservationRecorder
from parking_assistant.mcp.server import create_http_app
from parking_assistant.reservations.submission import ReservationSubmissionService

DEFAULT_OUTPUT = Path("evaluation/stage3_performance_report.json")
DEFAULT_MARKDOWN = Path("evaluation/stage3_performance_report.md")


class Stage3PerformanceReport(BaseModel):
    """Measured Stage 3 presentation-scale latency baseline."""

    model_config = ConfigDict(frozen=True)

    requested_samples_per_operation: int
    operations: list[OperationBenchmark]
    notes: list[str] = Field(default_factory=list)


class MemoryRows:
    """PII-bearing synthetic reader used only to isolate local file append cost."""

    def __init__(self, rows: list[ReservationRequest]) -> None:
        self._rows = {row.id: row for row in rows}

    def get(self, reservation_id: UUID) -> ReservationRequest:
        return self._rows[reservation_id]

    def get_facility_name(self, facility_id: UUID) -> str:
        if facility_id != FACILITY_ID:
            raise LookupError("synthetic facility not found")
        return "Central Station Parking"


def _memory_row(index: int) -> ReservationRequest:
    start = datetime.now(UTC) + timedelta(days=3, minutes=index)
    return ReservationRequest(
        id=uuid4(),
        first_name="Benchmark",
        last_name="Synthetic",
        car_number=f"S3FILE{index:04d}",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        facility_id=FACILITY_ID,
        status=ReservationRequestStatus.APPROVED,
        decision_at=datetime.now(UTC),
        decision_by="stage3-benchmark",
        idempotency_digest=uuid4().hex + uuid4().hex,
    )


def run_stage3_performance_baseline(
    settings: Settings,
    samples: int,
) -> Stage3PerformanceReport:  # pragma: no cover - real benchmark CLI
    """Measure real PostgreSQL and authenticated MCP plus isolated local file operations."""
    if samples <= 0:
        raise ValueError("samples must be positive")
    port = free_local_port()
    token = f"stage3-performance-{uuid4()}"
    with TemporaryDirectory(prefix="parking-stage3-performance-") as directory:
        root = Path(directory)
        benchmark_settings = settings.model_copy(
            update={
                "langsmith_tracing": False,
                "mcp_server_host": "127.0.0.1",
                "mcp_server_port": port,
                "mcp_server_token": SecretStr(token),
                "mcp_reservation_file": root / "mcp-confirmed.txt",
            }
        )
        engine = create_database_engine(benchmark_settings)
        factory = create_session_factory(engine)
        reservations = ReservationSubmissionService(factory, benchmark_settings)
        reservation_ids: list[UUID] = []

        def approved(label: str, offset: int) -> UUID:
            pending = reservations.submit_for_approval(
                synthetic_details(label, minute_offset=offset),
                facility_id=FACILITY_ID,
                idempotency_key=f"stage3-performance-{label}-{uuid4()}",
            )
            reservation_ids.append(pending.id)
            reservations.approve(pending.id, decision_by="stage3-benchmark")
            return pending.id

        try:
            with session_scope(factory) as session:
                seed_data(session)
            lookup_id = approved("lookup", 0)
            duplicate_id = approved("duplicate", 1)
            mcp_id = approved("mcp", 2)
            full_ids = [approved("full", 10 + index) for index in range(samples)]
            recorder = ApprovedReservationRecorder(
                reservations, benchmark_settings.mcp_reservation_file
            )
            recorder.record(duplicate_id)
            recorder.record(mcp_id)

            memory_rows = [_memory_row(index) for index in range(samples)]
            append_ids = [row.id for row in memory_rows]
            append_recorder = ApprovedReservationRecorder(
                MemoryRows(memory_rows), root / "isolated-file-append.txt"
            )

            app = create_http_app(settings=benchmark_settings, recorder=recorder)
            with running_server(app, port):
                client = ReservationMCPClient(benchmark_settings)
                operations = [
                    benchmark_operation(
                        "postgresql_authorization_lookup",
                        lambda: reservations.get(lookup_id),
                        samples,
                        network_dependent=False,
                    ),
                    benchmark_operation(
                        "file_append",
                        lambda: append_recorder.record(append_ids.pop()),
                        samples,
                        network_dependent=False,
                    ),
                    benchmark_operation(
                        "duplicate_idempotent_invocation",
                        lambda: recorder.record(duplicate_id),
                        samples,
                        network_dependent=False,
                    ),
                    benchmark_operation(
                        "mcp_tool_invocation",
                        lambda: client.record_if_approved(mcp_id),
                        samples,
                        network_dependent=True,
                    ),
                    benchmark_operation(
                        "full_approved_recording_path",
                        lambda: client.record_if_approved(full_ids.pop()),
                        samples,
                        network_dependent=True,
                    ),
                ]
        finally:
            if reservation_ids:
                with session_scope(factory) as session:
                    session.execute(
                        delete(ReservationRequest).where(
                            ReservationRequest.id.in_(reservation_ids)
                        )
                    )
            engine.dispose()

    return Stage3PerformanceReport(
        requested_samples_per_operation=samples,
        operations=operations,
        notes=[
            "PostgreSQL lookup uses the configured real database.",
            (
                "File append isolates serialization, locking, duplicate scan, flush, and fsync "
                "using an in-memory synthetic reader."
            ),
            (
                "MCP measurements use authenticated localhost Streamable HTTP and the "
                "LangChain MCP client."
            ),
            (
                "Full approved recording includes transport, PostgreSQL reload, authorization, "
                "and file recording."
            ),
            (
                "Measurements are presentation-scale observations in this environment, not "
                "SLA guarantees."
            ),
            "All database rows and temporary files are synthetic and removed after the run.",
        ],
    )


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(report: Stage3PerformanceReport) -> str:
    """Render the measured Stage 3 latency distribution."""
    lines = [
        "# Stage 3 performance baseline",
        "",
        "| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | HTTP boundary |",
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


def main() -> None:  # pragma: no cover - real benchmark CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    arguments = parser.parse_args()
    report = run_stage3_performance_baseline(get_settings(), arguments.samples)
    arguments.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    arguments.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
