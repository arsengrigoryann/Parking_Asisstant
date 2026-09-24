"""Local Presidio-based PII detection and deterministic redaction."""

from __future__ import annotations

import re

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NoOpNlpEngine
from presidio_analyzer.recognizer_registry import RecognizerRegistry
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from presidio_anonymizer.entities import RecognizerResult as AnonymizerRecognizerResult
from pydantic import BaseModel, ConfigDict

PERSON = "PERSON"
EMAIL_ADDRESS = "EMAIL_ADDRESS"
PHONE_NUMBER = "PHONE_NUMBER"
CAR_NUMBER = "CAR_NUMBER"
PII_ENTITIES = (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CAR_NUMBER)

# These are approved public place/facility names, not customer identities. Keeping the
# exclusions here makes false-positive handling explicit and shared by input/output checks.
PUBLIC_ENTITY_ALLOWLIST = frozenset(
    {
        "Central Station",
        "Central Station Parking",
        "Railway Square",
        "Yerevan Armenia",
        *(f"On {weekday}" for weekday in (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )),
    }
)


class PrivacyResult(BaseModel):
    """Safe result which deliberately contains no detected raw values or offsets."""

    model_config = ConfigDict(frozen=True)

    sanitized_text: str
    entity_types: tuple[str, ...] = ()
    pii_detected: bool = False


def _embedded_car_pattern(configured_pattern: str) -> str:
    """Turn the validator's full-match expression into a token recognizer expression."""
    body = configured_pattern
    if body.startswith("^"):
        body = body[1:]
    if body.endswith("$") and not body.endswith(r"\$"):
        body = body[:-1]
    return rf"(?<![A-Z0-9])(?:{body})(?![A-Z0-9])"


def _recognizers(car_number_pattern: str) -> list[PatternRecognizer]:
    """Create the centrally configured local recognizers used by Presidio."""
    flags = re.DOTALL | re.MULTILINE
    return [
        PatternRecognizer(
            supported_entity=EMAIL_ADDRESS,
            name="local-email-recognizer",
            patterns=[
                Pattern(
                    "email",
                    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
                    0.95,
                )
            ],
            global_regex_flags=flags,
        ),
        PatternRecognizer(
            supported_entity=PHONE_NUMBER,
            name="local-phone-recognizer",
            patterns=[
                Pattern(
                    "phone",
                    r"(?<!\w)(?:\+?\d[\d ().-]{6,}\d)(?!\w)",
                    0.99,
                )
            ],
            global_regex_flags=flags,
        ),
        PatternRecognizer(
            supported_entity=CAR_NUMBER,
            name="configured-car-number-recognizer",
            patterns=[
                Pattern(
                    "configured-car-number",
                    _embedded_car_pattern(car_number_pattern),
                    0.9,
                )
            ],
            global_regex_flags=flags,
        ),
        PatternRecognizer(
            supported_entity=PERSON,
            name="unicode-person-name-recognizer",
            patterns=[
                Pattern(
                    "contextual-two-part-unicode-name",
                    r"(?<=(?i:for|contact|name is|named)[ \t])"
                    r"\p{Lu}[\p{L}\p{M}'\u2019\u2013-]{1,49}[ \t]+"
                    r"\p{Lu}[\p{L}\p{M}'\u2019\u2013-]{1,49}"
                    r"(?![\p{L}\p{M}'\u2019\u2013-])",
                    0.95,
                ),
                Pattern(
                    "two-part-unicode-name",
                    r"(?<![\p{L}\p{M}'\u2019\u2013-])"
                    r"\p{Lu}[\p{L}\p{M}'\u2019\u2013-]{1,49}"
                    r"[ \t]+\p{Lu}[\p{L}\p{M}'\u2019\u2013-]{1,49}"
                    r"(?![\p{L}\p{M}'\u2019\u2013-])",
                    0.7,
                )
            ],
            global_regex_flags=flags,
        ),
    ]


class PrivacyService:
    """Reusable in-process PII detector/redactor with no external privacy service."""

    def __init__(self, car_number_pattern: str) -> None:
        # Presidio's no-op NLP engine keeps this service local and model-free. Detection is
        # intentionally deterministic; the configured recognizers remain Presidio recognizers.
        registry = RecognizerRegistry(
            recognizers=_recognizers(car_number_pattern),
            global_regex_flags=re.DOTALL | re.MULTILINE,
            supported_languages=["en"],
        )
        self._analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=NoOpNlpEngine(models=[{"lang_code": "en", "model_name": ""}]),
            supported_languages=["en"],
            log_decision_process=False,
        )
        self._anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]

    def inspect_and_redact(
        self,
        text: str,
        *,
        allowed_values: frozenset[str] = frozenset(),
    ) -> PrivacyResult:
        """Return redacted text and value-free metadata, honoring explicit safe values."""
        results = self._analyzer.analyze(text=text, language="en", entities=list(PII_ENTITIES))
        permitted = PUBLIC_ENTITY_ALLOWLIST | allowed_values
        permitted_spans = _value_spans(text, permitted)
        candidates = [
            result
            for result in results
            if text[result.start : result.end] not in permitted
            and not any(
                allowed_start <= result.start and result.end <= allowed_end
                for allowed_start, allowed_end in permitted_spans
            )
        ]
        filtered = _prefer_high_confidence_non_overlapping(candidates)
        if not filtered:
            return PrivacyResult(sanitized_text=text)
        anonymizer_findings = [
            AnonymizerRecognizerResult(
                entity_type=result.entity_type,
                start=result.start,
                end=result.end,
                score=result.score,
            )
            for result in filtered
        ]
        sanitized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=anonymizer_findings,
            operators={
                entity: OperatorConfig("replace", {"new_value": f"<{entity}>"})
                for entity in PII_ENTITIES
            },
        ).text
        entity_types = tuple(sorted({result.entity_type for result in filtered}))
        return PrivacyResult(
            sanitized_text=sanitized,
            entity_types=entity_types,
            pii_detected=True,
        )

def _prefer_high_confidence_non_overlapping(
    findings: list[RecognizerResult],
) -> list[RecognizerResult]:
    """Avoid expanding redaction when multiple heuristic findings overlap."""
    selected: list[RecognizerResult] = []
    for finding in sorted(
        findings,
        key=lambda item: (-item.score, -(item.end - item.start), item.start),
    ):
        if not any(
            finding.start < existing.end and existing.start < finding.end
            for existing in selected
        ):
            selected.append(finding)
    return sorted(selected, key=lambda item: item.start)


def _value_spans(text: str, values: frozenset[str]) -> list[tuple[int, int]]:
    """Locate explicitly allowed values without retaining additional PII state."""
    spans: list[tuple[int, int]] = []
    for value in values:
        start = text.find(value)
        while start >= 0:
            spans.append((start, start + len(value)))
            start = text.find(value, start + len(value))
    return spans
