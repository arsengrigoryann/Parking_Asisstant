"""Deterministic request and response security policies."""

from __future__ import annotations

import re

from parking_assistant.guardrails.privacy import PrivacyResult, PrivacyService
from parking_assistant.reservations.models import ReservationDetails

SECURITY_REFUSAL_MESSAGE = (
    "I can't provide private customer data, internal prompts, secrets, or raw system data. "
    "I can help with public parking information or your own reservation details."
)

_EXFILTRATION_PATTERNS = (
    re.compile(
        r"\b(?:dump|export|return|show|reveal|list)\b.{0,45}"
        r"\b(?:database|table|records?)\b",
        re.I,
    ),
    re.compile(r"\b(?:system|developer|hidden)\s+(?:prompt|instructions?)\b", re.I),
    re.compile(
        r"\b(?:all|every|another|other|previous)\b.{0,45}"
        r"\b(?:customers?|users?|reservations?)\b",
        re.I,
    ),
    re.compile(
        r"(?:\b(?:weaviate|vector(?:s| database| store)?)\b.{0,40}"
        r"\b(?:objects?|data|metadata|dump|return|show)\b|"
        r"\b(?:objects?|data|metadata|dump|return|show)\b.{0,40}\bweaviate\b)",
        re.I,
    ),
    re.compile(r"\bignore\b.{0,35}\b(?:previous|prior|system|instructions?)\b", re.I),
    re.compile(r"\b(?:secrets?|api[ -]?keys?|credentials?)\b", re.I),
)


def is_data_exfiltration_request(text: str) -> bool:
    """Recognize requests which may not reach models, retrieval, or business data."""
    return any(pattern.search(text) for pattern in _EXFILTRATION_PATTERNS)


class OutputGuardrail:
    """Inspect all application output with an explicit reservation-session exception."""

    def __init__(self, privacy: PrivacyService) -> None:
        self._privacy = privacy

    def general(self, text: str) -> PrivacyResult:
        """Redact unexpected PII from non-reservation output."""
        return self._privacy.inspect_and_redact(text)

    def reservation(
        self,
        text: str,
        details: ReservationDetails | None,
    ) -> PrivacyResult:
        """Allow only validated PII intentionally summarized for this active session."""
        allowed = (
            frozenset(
                {
                    details.first_name,
                    details.last_name,
                    f"{details.first_name} {details.last_name}",
                    details.car_number,
                    details.start_datetime.isoformat(),
                    details.end_datetime.isoformat(),
                }
            )
            if details is not None
            else frozenset()
        )
        return self._privacy.inspect_and_redact(text, allowed_values=allowed)
