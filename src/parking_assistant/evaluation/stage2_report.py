"""Build the final Stage 2 reports from executed performance and verification artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from parking_assistant.evaluation.stage2_performance import Stage2PerformanceReport

DEFAULT_PERFORMANCE = Path("evaluation/stage2_performance_report.json")
DEFAULT_VERIFICATION = Path("evaluation/stage2_verification_report.json")
DEFAULT_JSON = Path("evaluation/stage2_report.json")
DEFAULT_MARKDOWN = Path("evaluation/stage2_report.md")


class Stage2VerificationReport(BaseModel):
    """Results copied from the final executed Stage 2C quality gates."""

    model_config = ConfigDict(frozen=True)

    default_passed: int
    integration_skipped: int
    coverage_percent: float
    integration_passed: int
    approval_e2e_passed: bool
    rejection_e2e_passed: bool
    restart_durability_passed: bool
    checkpoint_privacy_passed: bool
    unauthenticated_access_rejected: bool
    admin_review_read_only: bool
    studio_graph_verified: bool
    ruff_passed: bool
    mypy_passed: bool


class Stage2Report(BaseModel):
    """Submission-facing Stage 2 architecture, evaluation, and limitations."""

    model_config = ConfigDict(frozen=True)

    scope: str
    architecture: list[str]
    performance: Stage2PerformanceReport
    verification: Stage2VerificationReport
    known_limitations: list[str]


def load_stage2_report(
    performance_path: Path = DEFAULT_PERFORMANCE,
    verification_path: Path = DEFAULT_VERIFICATION,
) -> Stage2Report:
    """Validate and combine only artifacts produced by executed Stage 2 checks."""
    return Stage2Report(
        scope=(
            "Stage 2 adds durable reservation requests, authenticated human decisions, "
            "read-only administrator assistance, and a restart-safe LangGraph interrupt/resume "
            "workflow. It does not book spaces or implement MCP."
        ),
        architecture=[
            "Explicit escalation submits one validated complete draft to PostgreSQL.",
            "A durable reservation/workflow/thread mapping addresses one PostgresSaver thread.",
            "The graph pauses at a real human interrupt and stores identifiers/status only.",
            "The authenticated administrator API is the only lifecycle decision authority.",
            "Resume input is non-authoritative; the graph re-reads PostgreSQL before routing.",
            "The LangChain review component is read-only, subordinate, and trace-disabled.",
        ],
        performance=Stage2PerformanceReport.model_validate_json(
            performance_path.read_text(encoding="utf-8")
        ),
        verification=Stage2VerificationReport.model_validate_json(
            verification_path.read_text(encoding="utf-8")
        ),
        known_limitations=[
            "Bearer-token administrator authentication is demo-grade and has no user accounts.",
            "Generated review text depends on OpenAI and is never authoritative.",
            "Approval records a decision but does not recheck capacity, allocate, or book a space.",
            "Reservation and checkpoint retention require a production deletion policy.",
            "A committed decision can require an idempotent retry if later graph resume fails.",
            "Measured model/network latency is environment-specific and is not an SLA.",
        ],
    )


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(report: Stage2Report) -> str:
    """Render the concise final report with all required Stage 2 evidence."""
    verification = report.verification
    lines = [
        "# Stage 2 final evaluation report",
        "",
        "## 1. Stage 2 architecture",
        "",
        report.scope,
        "",
        *(f"- {item}" for item in report.architecture),
        "",
        "## 2. Durable reservation lifecycle",
        "",
        "Validated drafts move from `PENDING_APPROVAL` to exactly one terminal decision. "
        "Opaque idempotency keys make repeated escalation and repeated identical decisions safe.",
        "",
        "## 3. Authenticated administrator interaction",
        "",
        "All review and decision routes require the configured bearer token. Authoritative "
        "reservation fields are loaded directly from PostgreSQL.",
        "",
        "## 4. Administrator LangChain review component",
        "",
        "The model produces presentation-only structured text with no tools or decision field. "
        "PII-bearing model calls run with tracing disabled and deterministic validators reject "
        "recommendations and availability guarantees.",
        "",
        "## 5. LangGraph human-in-the-loop workflow",
        "",
        "The real graph pauses at `wait_for_human`, resumes the same durable thread, and routes "
        "only after `verify_decision` reloads PostgreSQL.",
        "",
        "## 6. Database-authoritative decision model",
        "",
        "Only authenticated lifecycle endpoints can approve, reject, or cancel. Neither the "
        "administrator LLM nor LangGraph resume payload can authorize a transition.",
        "",
        "## 7. Durability and restart behavior",
        "",
        "Restart/resume integration: "
        f"{'passed' if verification.restart_durability_passed else 'failed'}. "
        "The tested workflow resumed by its persisted thread after rebuilding database and graph "
        "service objects.",
        "",
        "## 8. Privacy and checkpoint protections",
        "",
        f"Checkpoint PII scan: {'passed' if verification.checkpoint_privacy_passed else 'failed'}. "
        "Graph state, interrupt payloads, mappings, and checkpoints contain safe "
        "identifiers/status; "
        "raw customer fields remain in the reservation database.",
        "",
        "## 9. End-to-end validation",
        "",
        f"- Approval path: {'passed' if verification.approval_e2e_passed else 'failed'}",
        f"- Rejection path: {'passed' if verification.rejection_e2e_passed else 'failed'}",
        "- Unauthenticated access rejected: "
        f"{'passed' if verification.unauthenticated_access_rejected else 'failed'}",
        "- Read-only administrator review: "
        f"{'passed' if verification.admin_review_read_only else 'failed'}",
        f"- Studio graph discovery: {'passed' if verification.studio_graph_verified else 'failed'}",
        "",
        "## 10. Performance results",
        "",
        "| Operation | Samples | Average ms | p50 ms | p95 ms | Failures |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in report.performance.operations:
        lines.append(
            f"| {item.operation} | {item.sample_count} | {_metric(item.average_ms)} | "
            f"{_metric(item.p50_ms)} | {_metric(item.p95_ms)} | {item.failures} |"
        )
    lines.extend(
        [
            "",
            "OpenAI-dependent review generation is separated from deterministic operations. "
            "These measurements are an observed baseline, not an SLA.",
            "",
            "## 11. Tests and coverage",
            "",
            f"- Default suite: {verification.default_passed} passed; "
            f"{verification.integration_skipped} opt-in integrations skipped",
            f"- Branch coverage: {verification.coverage_percent:.2f}%",
            f"- Real integrations: {verification.integration_passed} passed",
            f"- Ruff: {'passed' if verification.ruff_passed else 'failed'}",
            f"- Strict mypy: {'passed' if verification.mypy_passed else 'failed'}",
            "",
            "## 12. Known limitations",
            "",
            *(f"- {item}" for item in report.known_limitations),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - report composition CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--performance", type=Path, default=DEFAULT_PERFORMANCE)
    parser.add_argument("--verification", type=Path, default=DEFAULT_VERIFICATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = load_stage2_report(args.performance, args.verification)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
