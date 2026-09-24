"""Deterministic Stage 1E security evaluation and JSON report CLI."""

from __future__ import annotations

import argparse
import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.config import get_settings
from parking_assistant.guardrails.privacy import PrivacyService
from parking_assistant.guardrails.security import is_data_exfiltration_request
from parking_assistant.reservations.service import is_explicit_reservation_start

DEFAULT_SECURITY_DATASET = Path("evaluation/security_dataset.jsonl")


class ExpectedBehavior(StrEnum):
    SANITIZE = "sanitize"
    ALLOW_RESERVATION = "allow_reservation"
    BLOCK = "block"
    ALLOW = "allow"


class SecurityEvaluationCase(BaseModel):
    """One synthetic deterministic security expectation."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    text: str = Field(min_length=1)
    expected_behavior: ExpectedBehavior
    sensitive_values: list[str] = Field(default_factory=list)


class SecurityMetrics(BaseModel):
    """Presentation-friendly aggregate metrics computed from executed cases."""

    model_config = ConfigDict(frozen=True)

    pii_leakage_rate: float
    blocked_malicious_request_rate: float
    benign_request_pass_rate: float
    cross_session_leakage_count: int
    total_cases: int
    passed: int
    failed: int
    failed_case_ids: list[str] = Field(default_factory=list)


def load_security_dataset(
    path: Path = DEFAULT_SECURITY_DATASET,
) -> list[SecurityEvaluationCase]:
    """Load and validate the synthetic JSONL security cases."""
    return [
        SecurityEvaluationCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_security(
    cases: list[SecurityEvaluationCase],
    privacy: PrivacyService,
) -> SecurityMetrics:
    """Execute local guardrails and report only observed outcomes."""
    if not cases:
        raise ValueError("security evaluation dataset must not be empty")
    passed_ids: list[str] = []
    failed_ids: list[str] = []
    sanitization_cases = 0
    pii_leaks = 0
    malicious_cases = 0
    malicious_blocked = 0
    benign_cases = 0
    benign_passed = 0
    cross_session_leaks = 0

    for case in cases:
        sanitized = privacy.inspect_and_redact(case.text)
        blocked = is_data_exfiltration_request(case.text)
        if case.expected_behavior is ExpectedBehavior.SANITIZE:
            sanitization_cases += 1
            leaked = any(value in sanitized.sanitized_text for value in case.sensitive_values)
            pii_leaks += int(leaked)
            case_passed = sanitized.pii_detected and not leaked and not blocked
        elif case.expected_behavior is ExpectedBehavior.ALLOW_RESERVATION:
            case_passed = is_explicit_reservation_start(case.text) and sanitized.pii_detected
        elif case.expected_behavior is ExpectedBehavior.BLOCK:
            malicious_cases += 1
            malicious_blocked += int(blocked)
            case_passed = blocked
        else:
            benign_cases += 1
            case_passed = not blocked and sanitized.sanitized_text == case.text
            benign_passed += int(case_passed)

        (passed_ids if case_passed else failed_ids).append(case.case_id)
        if case.category == "cross_session" and not case_passed:
            cross_session_leaks += 1

    return SecurityMetrics(
        pii_leakage_rate=pii_leaks / sanitization_cases if sanitization_cases else 0.0,
        blocked_malicious_request_rate=(
            malicious_blocked / malicious_cases if malicious_cases else 0.0
        ),
        benign_request_pass_rate=benign_passed / benign_cases if benign_cases else 0.0,
        cross_session_leakage_count=cross_session_leaks,
        total_cases=len(cases),
        passed=len(passed_ids),
        failed=len(failed_ids),
        failed_case_ids=failed_ids,
    )


def main() -> None:  # pragma: no cover - exercised as a CLI during verification
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_SECURITY_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    metrics = evaluate_security(
        load_security_dataset(args.dataset),
        PrivacyService(settings.reservation_car_number_pattern),
    )
    report = json.dumps(metrics.model_dump(), indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":  # pragma: no cover
    main()
