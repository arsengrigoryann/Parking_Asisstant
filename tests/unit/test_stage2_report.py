"""Tests for the concise final Stage 2 report."""

from parking_assistant.evaluation.performance import OperationBenchmark
from parking_assistant.evaluation.stage2_performance import Stage2PerformanceReport
from parking_assistant.evaluation.stage2_report import (
    Stage2Report,
    Stage2VerificationReport,
    render_markdown,
)


def _report() -> Stage2Report:
    return Stage2Report(
        scope="Stage 2 scope.",
        architecture=["Human authority."],
        performance=Stage2PerformanceReport(
            requested_samples_per_operation=2,
            operations=[
                OperationBenchmark(
                    operation="graph_resume_after_decision",
                    sample_count=2,
                    successes=2,
                    failures=0,
                    average_ms=10,
                    p50_ms=9,
                    p95_ms=11,
                    network_dependent=False,
                )
            ],
        ),
        verification=Stage2VerificationReport(
            default_passed=140,
            integration_skipped=8,
            coverage_percent=91,
            integration_passed=8,
            approval_e2e_passed=True,
            rejection_e2e_passed=True,
            restart_durability_passed=True,
            checkpoint_privacy_passed=True,
            unauthenticated_access_rejected=True,
            admin_review_read_only=True,
            studio_graph_verified=True,
            ruff_passed=True,
            mypy_passed=True,
        ),
        known_limitations=["Measured limitation."],
    )


def test_markdown_contains_all_required_sections() -> None:
    markdown = render_markdown(_report())

    for number in range(1, 13):
        assert f"## {number}." in markdown


def test_markdown_uses_measured_verification_and_performance_values() -> None:
    markdown = render_markdown(_report())

    assert "140 passed" in markdown
    assert "91.00%" in markdown
    assert "graph_resume_after_decision" in markdown
    assert "not an SLA" in markdown
