"""Tests for Stage 2 performance report rendering."""

from parking_assistant.evaluation.performance import OperationBenchmark
from parking_assistant.evaluation.stage2_performance import (
    Stage2PerformanceReport,
    render_markdown,
)


def _report() -> Stage2PerformanceReport:
    return Stage2PerformanceReport(
        requested_samples_per_operation=3,
        operations=[
            OperationBenchmark(
                operation="workflow_start_until_interrupt",
                sample_count=3,
                successes=3,
                failures=0,
                average_ms=12,
                p50_ms=11,
                p95_ms=14,
                network_dependent=False,
            )
        ],
        notes=["Measured note."],
    )


def test_markdown_reports_distribution_and_failures() -> None:
    markdown = render_markdown(_report())

    assert "workflow_start_until_interrupt" in markdown
    assert "12.00" in markdown
    assert "| 0 |" in markdown


def test_markdown_labels_external_dependency_and_notes() -> None:
    report = _report().model_copy(
        update={
            "operations": [
                _report().operations[0].model_copy(update={"network_dependent": True})
            ]
        }
    )

    markdown = render_markdown(report)
    assert "| yes |" in markdown
    assert "Measured note." in markdown
