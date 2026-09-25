"""Tests for Stage 3 evidence parsing and report rendering."""

from parking_assistant.evaluation.performance import OperationBenchmark
from parking_assistant.evaluation.stage3_performance import Stage3PerformanceReport
from parking_assistant.evaluation.stage3_performance import (
    render_markdown as render_performance,
)
from parking_assistant.evaluation.stage3_report import (
    QualityGates,
    build_report,
    render_markdown,
)
from parking_assistant.evaluation.stage3_verification import (
    Stage3VerificationReport,
    inspector_invocation_verified,
    inspector_tool_list_verified,
)


def verification() -> Stage3VerificationReport:
    return Stage3VerificationReport(
        approved_cases_passed=2,
        unauthorized_cases_tested=4,
        unauthorized_cases_blocked=4,
        unauthorized_states=["PENDING_APPROVAL", "REJECTED", "CANCELLED", "UNKNOWN"],
        duplicate_records_created=0,
        concurrent_duplicate_records_created=0,
        authentication_rejections=1,
        configured_path_enforced=True,
        transport_success=True,
        transport_failures=0,
        retry_after_temporary_failure_passed=True,
        file_format_checks=6,
        file_format_passed=True,
        tool_count=1,
        tool_names=["record_approved_reservation"],
        tool_input_fields=["reservation_id"],
        inspector_cli_verified=True,
        inspector_tool_list_verified=True,
        inspector_invocation_verified=True,
    )


def performance() -> Stage3PerformanceReport:
    return Stage3PerformanceReport(
        requested_samples_per_operation=3,
        operations=[
            OperationBenchmark(
                operation="mcp_tool_invocation",
                sample_count=3,
                successes=3,
                failures=0,
                average_ms=10.0,
                p50_ms=9.0,
                p95_ms=12.0,
                network_dependent=True,
            )
        ],
        notes=["Measured locally; not an SLA."],
    )


def test_inspector_tool_parser_requires_bounded_schema() -> None:
    valid = '{"name":"record_approved_reservation","properties":{"reservation_id":{}}}'

    assert inspector_tool_list_verified(valid)
    assert not inspector_tool_list_verified(valid + ' "first_name"')


def test_inspector_result_parser_accepts_only_recording_outcomes() -> None:
    assert inspector_invocation_verified('{"outcome":"already_recorded"}')
    assert inspector_invocation_verified('{"outcome":"recorded"}')
    assert not inspector_invocation_verified('{"status":"approved"}')


def test_performance_markdown_reports_distribution_and_failures() -> None:
    rendered = render_performance(performance())

    assert "Average ms" in rendered
    assert "p50 ms" in rendered
    assert "p95 ms" in rendered
    assert "| mcp_tool_invocation | 3 | 0 | 10.00 | 9.00 | 12.00 | yes |" in rendered


def test_performance_report_retains_non_sla_note() -> None:
    report = performance()

    assert report.requested_samples_per_operation == 3
    assert "not an SLA" in report.notes[0]


def test_final_report_contains_required_executed_evidence() -> None:
    report = build_report(
        verification(),
        performance(),
        QualityGates(
            ruff="passed",
            mypy="passed",
            default_tests="10 passed",
            integration_tests="2 passed",
        ),
    )
    rendered = render_markdown(report)

    assert "Unauthorized cases blocked | 4/4" in rendered
    assert "Concurrent duplicate records created | 0" in rendered
    assert "Official Inspector CLI passed: passed" in rendered
    assert "not SLA" in rendered


def test_final_report_json_is_machine_readable_and_excludes_coverage_metric() -> None:
    report = build_report(
        verification(),
        performance(),
        QualityGates(
            ruff="passed",
            mypy="passed",
            default_tests="10 passed",
            integration_tests="2 passed",
        ),
    )

    assert report.model_dump(mode="json")["verification"]["tool_input_fields"] == [
        "reservation_id"
    ]
    assert report.quality_gates.coverage == "not collected by project policy"
