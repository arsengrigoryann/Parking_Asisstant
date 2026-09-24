"""Build the unified Stage 1 JSON and Markdown reports from executed artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from parking_assistant.evaluation.answer_quality import AnswerQualityReport
from parking_assistant.evaluation.performance import PerformanceReport
from parking_assistant.guardrails.evaluation import SecurityMetrics
from parking_assistant.rag.evaluation import RetrievalEvaluationReport

DEFAULT_RETRIEVAL = Path("evaluation/retrieval_report.json")
DEFAULT_ANSWER_QUALITY = Path("evaluation/answer_quality_report.json")
DEFAULT_PERFORMANCE = Path("evaluation/performance_report.json")
DEFAULT_SECURITY = Path("evaluation/security_report.json")
DEFAULT_VERIFICATION = Path("evaluation/verification_report.json")
DEFAULT_JSON = Path("evaluation/stage1_report.json")
DEFAULT_MARKDOWN = Path("evaluation/stage1_report.md")


class VerificationReport(BaseModel):
    """Results copied from the final executed quality gates."""

    model_config = ConfigDict(frozen=True)

    unit_passed: int
    integration_skipped: int
    coverage_percent: float
    integration_passed: int
    ruff_passed: bool
    mypy_passed: bool


class Stage1Report(BaseModel):
    """Unified machine-readable Stage 1 evidence."""

    model_config = ConfigDict(frozen=True)

    scope: str
    retrieval: RetrievalEvaluationReport
    answer_quality: AnswerQualityReport
    performance: PerformanceReport
    security: SecurityMetrics
    verification: VerificationReport
    known_limitations: list[str]


def load_stage1_report(
    retrieval_path: Path = DEFAULT_RETRIEVAL,
    answer_quality_path: Path = DEFAULT_ANSWER_QUALITY,
    performance_path: Path = DEFAULT_PERFORMANCE,
    security_path: Path = DEFAULT_SECURITY,
    verification_path: Path = DEFAULT_VERIFICATION,
) -> Stage1Report:
    """Validate and combine only reports produced by executed evaluation commands."""
    return Stage1Report(
        scope=(
            "Stage 1 user assistant: public static RAG, deterministic PostgreSQL reads, "
            "intent routing, ephemeral reservation detail collection, and privacy/security "
            "guardrails. No approval, persistence, LangGraph, or MCP."
        ),
        retrieval=RetrievalEvaluationReport.model_validate_json(
            retrieval_path.read_text(encoding="utf-8")
        ),
        answer_quality=AnswerQualityReport.model_validate_json(
            answer_quality_path.read_text(encoding="utf-8")
        ),
        performance=PerformanceReport.model_validate_json(
            performance_path.read_text(encoding="utf-8")
        ),
        security=SecurityMetrics.model_validate_json(
            security_path.read_text(encoding="utf-8")
        ),
        verification=VerificationReport.model_validate_json(
            verification_path.read_text(encoding="utf-8")
        ),
        known_limitations=[
            "OpenAI and Weaviate latency is network-dependent and varies by environment.",
            "Answer-quality scores are model-judged against a small curated public dataset.",
            "PII and injection detection are defense in depth and cannot cover every phrasing.",
            "Reservation state is process-local, unauthenticated, non-durable, and not booked.",
            "Stage 1 has no interval conflict check, human approval, persistence, "
            "LangGraph, or MCP.",
        ],
    )


def _format_latency(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(report: Stage1Report) -> str:
    """Render the concise submission-facing report from validated underlying results."""
    lines = [
        "# Stage 1 final evaluation report",
        "",
        "## 1. System scope",
        "",
        report.scope,
        "",
        "## 2. Retrieval evaluation",
        "",
        f"Dataset: {report.retrieval.dataset_size} queries; K={report.retrieval.k}.",
        "",
        "| Mode | Precision@K | Recall@K | MRR |",
        "|---|---:|---:|---:|",
    ]
    for mode, metrics in report.retrieval.results.items():
        lines.append(
            f"| {mode.value} | {metrics.precision_at_k:.3f} | "
            f"{metrics.recall_at_k:.3f} | {metrics.mrr:.3f} |"
        )
    lines.extend(
        [
            "",
            "The application uses hybrid retrieval: it combines semantic matching with exact "
            "term signals while retaining the mandatory public-data filter.",
            "",
            "## 3. Answer-quality evaluation",
            "",
            f"Cases: {report.answer_quality.completed}/{report.answer_quality.dataset_size}; "
            f"failures: {report.answer_quality.failures}.",
            "",
            "| Correctness | Groundedness | Relevance |",
            "|---:|---:|---:|",
            f"| {report.answer_quality.average_correctness:.3f} | "
            f"{report.answer_quality.average_groundedness:.3f} | "
            f"{report.answer_quality.average_relevance:.3f} |",
            "",
            "LangSmith experiment: "
            f"`{report.answer_quality.langsmith_experiment or 'not published'}`.",
            "",
            "## 4. Performance baseline",
            "",
            "| Operation | Samples | Avg ms | p50 ms | p95 ms | Failures |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report.performance.operations:
        lines.append(
            f"| {item.operation} | {item.sample_count} | {_format_latency(item.average_ms)} | "
            f"{_format_latency(item.p50_ms)} | {_format_latency(item.p95_ms)} | "
            f"{item.failures} |"
        )
    lines.extend(
        [
            "",
            "Network-dependent measurements are a reproducible baseline, not deterministic "
            "service-level guarantees.",
            "",
            "## 5. Security evaluation",
            "",
            f"- Cases passed: {report.security.passed}/{report.security.total_cases}",
            f"- PII leakage rate: {report.security.pii_leakage_rate:.3f}",
            "- Malicious-request block rate: "
            f"{report.security.blocked_malicious_request_rate:.3f}",
            f"- Benign-request pass rate: {report.security.benign_request_pass_rate:.3f}",
            f"- Cross-session leakage count: {report.security.cross_session_leakage_count}",
            "",
            "## 6. Test and coverage status",
            "",
            f"- Unit/default suite: {report.verification.unit_passed} passed; "
            f"{report.verification.integration_skipped} opt-in tests skipped",
            f"- Coverage: {report.verification.coverage_percent:.2f}%",
            f"- Real integrations: {report.verification.integration_passed} passed",
            f"- Ruff: {'passed' if report.verification.ruff_passed else 'failed'}",
            f"- Strict mypy: {'passed' if report.verification.mypy_passed else 'failed'}",
            "",
            "## 7. Known limitations",
            "",
            *(f"- {item}" for item in report.known_limitations),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - report composition CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = load_stage1_report()
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
