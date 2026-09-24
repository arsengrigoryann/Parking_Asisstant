"""Reproducible Stage 1 latency baseline for representative component boundaries."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.application import AssistantService
from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.queries import DynamicParkingService
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.guardrails.privacy import PrivacyService
from parking_assistant.rag.answering import GroundedRAGService
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.retrieval import RetrievalMode, RetrievalService
from parking_assistant.rag.weaviate import connect_weaviate
from parking_assistant.routing import IntentRouter

DEFAULT_OUTPUT = Path("evaluation/performance_report.json")
DEFAULT_MARKDOWN = Path("evaluation/performance_report.md")


class OperationBenchmark(BaseModel):
    """Observed latency distribution for one operation."""

    model_config = ConfigDict(frozen=True)

    operation: str
    sample_count: int
    successes: int
    failures: int
    average_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    network_dependent: bool


class PerformanceReport(BaseModel):
    """Machine-readable Stage 1 performance baseline."""

    model_config = ConfigDict(frozen=True)

    requested_samples_per_operation: int
    operations: list[OperationBenchmark]
    notes: list[str] = Field(default_factory=list)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def benchmark_operation(
    name: str,
    operation: Callable[[], object],
    samples: int,
    *,
    network_dependent: bool,
) -> OperationBenchmark:
    """Measure one callable repeatedly while retaining failures as measured data."""
    if samples <= 0:
        raise ValueError("samples must be positive")
    latencies: list[float] = []
    failures = 0
    for _ in range(samples):
        started = perf_counter()
        try:
            operation()
        except Exception:
            failures += 1
        else:
            latencies.append((perf_counter() - started) * 1000)
    return OperationBenchmark(
        operation=name,
        sample_count=samples,
        successes=len(latencies),
        failures=failures,
        average_ms=sum(latencies) / len(latencies) if latencies else None,
        p50_ms=_percentile(latencies, 0.50) if latencies else None,
        p95_ms=_percentile(latencies, 0.95) if latencies else None,
        network_dependent=network_dependent,
    )


def run_performance_baseline(
    settings: Settings,
    samples: int,
) -> PerformanceReport:  # pragma: no cover - exercised by the real benchmark CLI
    """Measure retrieval, RAG, SQL, routing, and complete request boundaries."""
    benchmark_settings = settings.model_copy(update={"langsmith_tracing": False})
    chat_model = create_chat_model(benchmark_settings)
    engine = create_database_engine(benchmark_settings)
    try:
        dynamic = DynamicParkingService(create_session_factory(engine))
        router = IntentRouter(chat_model)
        privacy = PrivacyService(benchmark_settings.reservation_car_number_pattern)
        with connect_weaviate(benchmark_settings) as client:
            retrieval = RetrievalService(
                ensure_public_knowledge_collection(client, benchmark_settings),
                create_embeddings(benchmark_settings),
                benchmark_settings,
            )
            answerer = GroundedRAGService(retrieval, chat_model, benchmark_settings)
            assistant = AssistantService(
                router,
                answerer,
                dynamic,
                benchmark_settings,
                privacy,
            )
            operations = [
                benchmark_operation(
                    "hybrid_retrieval",
                    lambda: retrieval.search(
                        "Where is the parking located?", RetrievalMode.HYBRID
                    ),
                    samples,
                    network_dependent=True,
                ),
                benchmark_operation(
                    "static_rag_request",
                    lambda: answerer.answer("What is the cancellation policy?"),
                    samples,
                    network_dependent=True,
                ),
                benchmark_operation(
                    "dynamic_postgresql_request",
                    dynamic.availability,
                    samples,
                    network_dependent=False,
                ),
                benchmark_operation(
                    "intent_classification",
                    lambda: router.classify("What does parking cost?"),
                    samples,
                    network_dependent=True,
                ),
                benchmark_operation(
                    "complete_assistant_request",
                    lambda: assistant.answer_query(
                        "How many parking spaces are currently available?"
                    ),
                    samples,
                    network_dependent=True,
                ),
            ]
    finally:
        engine.dispose()
    return PerformanceReport(
        requested_samples_per_operation=samples,
        operations=operations,
        notes=[
            "OpenAI and Weaviate network latency is environment-dependent, not deterministic.",
            "PostgreSQL measurements use the configured local Stage 1 database.",
            "Tracing is disabled during the benchmark to avoid observability overhead.",
        ],
    )


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(report: PerformanceReport) -> str:
    """Render measured benchmark results as a compact Markdown table."""
    lines = [
        "# Stage 1 performance baseline",
        "",
        "| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | Network-dependent |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            [
                item.operation,
                str(item.sample_count),
                str(item.failures),
                _metric(item.average_ms),
                _metric(item.p50_ms),
                _metric(item.p95_ms),
                "yes" if item.network_dependent else "no",
            ]
        )
        + " |"
        for item in report.operations
    )
    lines.extend(["", *(f"- {note}" for note in report.notes), ""])
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - real service benchmark CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = run_performance_baseline(get_settings(), args.samples)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report.model_dump(mode="json"), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
