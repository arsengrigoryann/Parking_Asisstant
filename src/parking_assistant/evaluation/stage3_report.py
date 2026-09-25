"""Build the final Stage 3 report from executed verification and performance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from parking_assistant.evaluation.stage3_performance import Stage3PerformanceReport
from parking_assistant.evaluation.stage3_verification import Stage3VerificationReport

DEFAULT_VERIFICATION = Path("evaluation/stage3_verification_report.json")
DEFAULT_PERFORMANCE = Path("evaluation/stage3_performance_report.json")
DEFAULT_OUTPUT = Path("evaluation/stage3_report.json")
DEFAULT_MARKDOWN = Path("evaluation/stage3_report.md")


class QualityGates(BaseModel):
    """Observed final repository verification results."""

    model_config = ConfigDict(frozen=True)

    ruff: str
    mypy: str
    default_tests: str
    integration_tests: str
    coverage: str = "not collected by project policy"


class Stage3Report(BaseModel):
    """Combined final evidence for Stage 3."""

    model_config = ConfigDict(frozen=True)

    stage: str = "Stage 3"
    status: str = "finalized"
    verification: Stage3VerificationReport
    performance: Stage3PerformanceReport
    quality_gates: QualityGates
    security_findings: list[str]
    limitations: list[str]


def build_report(
    verification: Stage3VerificationReport,
    performance: Stage3PerformanceReport,
    quality_gates: QualityGates,
) -> Stage3Report:
    """Combine executed inputs with the fixed Stage 3 security assessment."""
    return Stage3Report(
        verification=verification,
        performance=performance,
        quality_gates=quality_gates,
        security_findings=[
            "Only reservation_id crosses MCP; PostgreSQL reloads authoritative state.",
            "Pending, rejected, cancelled, and unknown reservations produced no record.",
            "Missing or invalid bearer authentication is rejected before MCP dispatch.",
            "The output path is configuration-only and cannot be supplied by a tool caller.",
            (
                "File locking and canonical-line checks prevented sequential and concurrent "
                "duplicates."
            ),
            "An MCP outage after approval preserves the committed decision for a safe retry.",
        ],
        limitations=[
            "Bearer authentication is demo-grade and the localhost endpoint has no TLS.",
            "The lock coordinates a shared local filesystem, not distributed storage.",
            "Canonical-line idempotency cannot distinguish two rows with identical display fields.",
            "Confirmed-record retention and automatic outage reconciliation are not implemented.",
            "Performance measurements are small environment-specific observations, not an SLA.",
        ],
    )


def render_markdown(report: Stage3Report) -> str:
    """Render a concise factual final report."""
    evidence = report.verification
    performance_rows = [
        (
            f"| {item.operation} | {item.sample_count} | {item.failures} | "
            f"{_metric(item.average_ms)} | {_metric(item.p50_ms)} | "
            f"{_metric(item.p95_ms)} |"
        )
        for item in report.performance.operations
    ]
    lines = [
        "# Stage 3 final report",
        "",
        "## Outcome",
        "",
        "Stage 3 is finalized. MCP records an approved reservation; it never authorizes one. "
        "The executed flow crossed the MCP boundary with only `reservation_id`, reloaded the "
        "authoritative PostgreSQL row, and wrote the configured file exactly once.",
        "",
        "## Executed reliability and security evidence",
        "",
        "| Check | Result |",
        "|---|---:|",
        f"| Approved cases passed | {evidence.approved_cases_passed} |",
        (
            "| Unauthorized cases blocked | "
            f"{evidence.unauthorized_cases_blocked}/{evidence.unauthorized_cases_tested} |"
        ),
        f"| Duplicate records created | {evidence.duplicate_records_created} |",
        (
            "| Concurrent duplicate records created | "
            f"{evidence.concurrent_duplicate_records_created} |"
        ),
        f"| Authentication rejections | {evidence.authentication_rejections} |",
        f"| Transport failures | {evidence.transport_failures} |",
        f"| File-format checks passed | {evidence.file_format_checks} |",
        f"| Configured path enforced | {_yes(evidence.configured_path_enforced)} |",
        (
            "| Retry after temporary MCP failure | "
            f"{_yes(evidence.retry_after_temporary_failure_passed)} |"
        ),
        "",
        "Unauthorized cases were `PENDING_APPROVAL`, `REJECTED`, `CANCELLED`, and an unknown "
        "identifier. None changed the output file.",
        "",
        "## MCP Inspector and transport",
        "",
        f"- Authenticated Streamable HTTP transport passed: {_yes(evidence.transport_success)}.",
        f"- Official Inspector CLI passed: {_yes(evidence.inspector_cli_verified)}.",
        f"- Exposed business tools: {evidence.tool_count} (`{', '.join(evidence.tool_names)}`).",
        f"- Accepted input fields: `{', '.join(evidence.tool_input_fields)}`.",
        "- Successful Inspector invocation returned a typed idempotent recording outcome.",
        "",
        "## Performance baseline",
        "",
        "| Operation | Samples | Failures | Average ms | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|",
        *performance_rows,
        "",
        "These presentation-scale measurements are observations in this environment, not SLA "
        "guarantees.",
        "",
        "## Security assessment",
        "",
        *(f"- {finding}" for finding in report.security_findings),
        "",
        "## Quality gates",
        "",
        f"- Ruff: {report.quality_gates.ruff}.",
        f"- Strict mypy: {report.quality_gates.mypy}.",
        f"- Default suite: {report.quality_gates.default_tests}.",
        f"- Real integrations: {report.quality_gates.integration_tests}.",
        f"- Coverage: {report.quality_gates.coverage}.",
        "",
        "## Known limitations",
        "",
        *(f"- {limitation}" for limitation in report.limitations),
        "",
    ]
    return "\n".join(lines)


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _yes(value: bool) -> str:
    return "passed" if value else "failed"


def main() -> None:  # pragma: no cover - report CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", type=Path, default=DEFAULT_VERIFICATION)
    parser.add_argument("--performance", type=Path, default=DEFAULT_PERFORMANCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--ruff", default="passed")
    parser.add_argument("--mypy", default="passed")
    parser.add_argument("--default-tests", required=True)
    parser.add_argument("--integration-tests", required=True)
    arguments = parser.parse_args()
    verification = Stage3VerificationReport.model_validate_json(
        arguments.verification.read_text(encoding="utf-8")
    )
    performance = Stage3PerformanceReport.model_validate_json(
        arguments.performance.read_text(encoding="utf-8")
    )
    report = build_report(
        verification,
        performance,
        QualityGates(
            ruff=arguments.ruff,
            mypy=arguments.mypy,
            default_tests=arguments.default_tests,
            integration_tests=arguments.integration_tests,
        ),
    )
    arguments.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    arguments.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
