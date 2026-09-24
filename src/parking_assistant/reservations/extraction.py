"""Structured LLM extraction of reservation fields from one message."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import tracing_context
from pydantic import ValidationError

from parking_assistant.reservations.models import ReservationExtraction, ReservationField

EXTRACTION_POLICY = """Extract reservation details from only the current user message.
Return null for every value not actually supplied. Resolve relative dates using the supplied
reference time and facility timezone. Datetimes may be extracted but must not be judged valid.
If a date is supplied without a specific start or end time, leave that datetime null.
Do not infer names, car numbers, dates, or times. correction_fields must contain only fields
the user clearly corrects with wording such as 'actually', 'change', or 'instead'. A message
may contain one, several, or all fields. Do not include explanations."""


class ReservationExtractionError(RuntimeError):
    """Raised when structured reservation extraction is invalid."""


class ReservationExtractor:
    """Use the central chat model for extraction, never business validation."""

    def __init__(self, chat_model: Any) -> None:
        self._extractor = chat_model.with_structured_output(
            ReservationExtraction,
            method="json_schema",
        )

    def extract(
        self,
        message: str,
        *,
        timezone_name: str,
        reference_time: datetime,
        collected_fields: set[ReservationField],
    ) -> ReservationExtraction:
        """Extract only current-turn values with non-PII temporal/state context."""
        if not message.strip():
            raise ValueError("reservation message must not be blank")
        context = (
            f"Facility timezone: {timezone_name}\n"
            f"Reference time: {reference_time.isoformat()}\n"
            "Already collected field names: "
            + (", ".join(sorted(field.value for field in collected_fields)) or "none")
        )
        try:
            # Reservation PII is deliberately excluded from LangSmith traces in Stage 1D.
            with tracing_context(enabled=False):
                raw = self._extractor.invoke(
                    [
                        SystemMessage(content=EXTRACTION_POLICY),
                        HumanMessage(content=f"{context}\n\nCurrent user message:\n{message}"),
                    ]
                )
            extraction = (
                raw
                if isinstance(raw, ReservationExtraction)
                else ReservationExtraction.model_validate(raw)
            )
            if not _has_explicit_time(message):
                extraction = extraction.model_copy(
                    update={"start_datetime": None, "end_datetime": None}
                )
            return extraction
        except (TypeError, ValueError, ValidationError) as error:
            raise ReservationExtractionError(
                "chat model returned invalid reservation extraction"
            ) from error


_CLOCK_TIME = re.compile(
    r"(?:\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b(?:noon|midnight)\b|"
    r"\b(?:[01]?\d|2[0-3])\s*(?:am|pm)\b|"
    r"\bfrom\s+(?:[01]?\d|2[0-3])\b.*\b(?:to|until)\s+(?:[01]?\d|2[0-3])\b)",
    re.IGNORECASE,
)


def _has_explicit_time(message: str) -> bool:
    """Require a user-supplied clock expression before accepting extracted datetimes."""
    return bool(_CLOCK_TIME.search(message))
