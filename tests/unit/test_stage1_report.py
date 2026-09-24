"""Tests for unified Stage 1 report composition."""

from parking_assistant.evaluation.answer_quality import AnswerQualityReport
from parking_assistant.evaluation.performance import OperationBenchmark, PerformanceReport
from parking_assistant.evaluation.stage1_report import (
    Stage1Report,
    VerificationReport,
    render_markdown,
)
from parking_assistant.guardrails.evaluation import SecurityMetrics
from parking_assistant.rag.evaluation import EvaluationMetrics, RetrievalEvaluationReport
from parking_assistant.rag.retrieval import RetrievalMode


def report() -> Stage1Report:
    return Stage1Report(
        scope="Stage 1 scope.",
        retrieval=RetrievalEvaluationReport(
            dataset_size=18,
            k=5,
            results={
                mode: EvaluationMetrics(precision_at_k=0.2, recall_at_k=1.0, mrr=1.0)
                for mode in RetrievalMode
            },
        ),
        answer_quality=AnswerQualityReport(
            dataset_size=12,
            completed=12,
            failures=0,
            average_correctness=0.9,
            average_groundedness=0.95,
            average_relevance=1.0,
            results=[],
        ),
        performance=PerformanceReport(
            requested_samples_per_operation=2,
            operations=[
                OperationBenchmark(
                    operation="hybrid_retrieval",
                    sample_count=2,
                    successes=2,
                    failures=0,
                    average_ms=10,
                    p50_ms=9,
                    p95_ms=11,
                    network_dependent=True,
                )
            ],
        ),
        security=SecurityMetrics(
            pii_leakage_rate=0,
            blocked_malicious_request_rate=1,
            benign_request_pass_rate=1,
            cross_session_leakage_count=0,
            total_cases=20,
            passed=20,
            failed=0,
        ),
        verification=VerificationReport(
            unit_passed=100,
            integration_skipped=5,
            coverage_percent=91,
            integration_passed=5,
            ruff_passed=True,
            mypy_passed=True,
        ),
        known_limitations=["Measured limitation."],
    )


def test_markdown_contains_every_required_report_section() -> None:
    markdown = render_markdown(report())

    for heading in (
        "System scope",
        "Retrieval evaluation",
        "Answer-quality evaluation",
        "Performance baseline",
        "Security evaluation",
        "Test and coverage status",
        "Known limitations",
    ):
        assert heading in markdown


def test_markdown_identifies_hybrid_application_mode_and_measured_values() -> None:
    markdown = render_markdown(report())

    assert "application uses hybrid retrieval" in markdown
    assert "20/20" in markdown
    assert "91.00%" in markdown
