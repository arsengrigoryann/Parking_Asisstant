"""Unit tests for deterministic performance aggregation."""

import pytest

from parking_assistant.evaluation.performance import (
    PerformanceReport,
    benchmark_operation,
    render_markdown,
)


def test_benchmark_reports_samples_latency_and_no_failures() -> None:
    result = benchmark_operation("fast", lambda: None, 3, network_dependent=False)

    assert result.sample_count == 3
    assert result.successes == 3
    assert result.failures == 0
    assert result.average_ms is not None
    assert result.p50_ms is not None
    assert result.p95_ms is not None


def test_benchmark_records_failures_and_validates_count() -> None:
    def fail() -> None:
        raise RuntimeError("expected")

    result = benchmark_operation("failure", fail, 2, network_dependent=True)

    assert result.successes == 0
    assert result.failures == 2
    assert result.average_ms is None
    report = PerformanceReport(requested_samples_per_operation=2, operations=[result])
    assert "failure" in render_markdown(report)
    with pytest.raises(ValueError, match="positive"):
        benchmark_operation("invalid", lambda: None, 0, network_dependent=False)
