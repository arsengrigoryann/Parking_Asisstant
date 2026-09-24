"""Validated structured intent classification without autonomous agent behavior."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from parking_assistant.db.models import Weekday


class IntentRoute(StrEnum):
    STATIC_INFORMATION = "STATIC_INFORMATION"
    DYNAMIC_INFORMATION = "DYNAMIC_INFORMATION"
    RESERVATION = "RESERVATION"
    UNSUPPORTED = "UNSUPPORTED"


class DynamicSubtype(StrEnum):
    AVAILABILITY = "AVAILABILITY"
    OPENING_HOURS = "OPENING_HOURS"
    PRICING = "PRICING"


class IntentDecision(BaseModel):
    """Strict structured output of the intent classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: IntentRoute
    dynamic_subtype: DynamicSubtype | None = None
    weekday: Weekday | None = None

    @model_validator(mode="after")
    def validate_dynamic_fields(self) -> IntentDecision:
        if self.route is IntentRoute.DYNAMIC_INFORMATION and self.dynamic_subtype is None:
            raise ValueError("dynamic_subtype is required for DYNAMIC_INFORMATION")
        if self.route is not IntentRoute.DYNAMIC_INFORMATION and self.dynamic_subtype is not None:
            raise ValueError("dynamic_subtype is valid only for DYNAMIC_INFORMATION")
        if self.dynamic_subtype is not DynamicSubtype.OPENING_HOURS and self.weekday is not None:
            raise ValueError("weekday is valid only for OPENING_HOURS")
        return self


ROUTER_POLICY = """Classify one user request for a parking assistant.
Routes:
- STATIC_INFORMATION: location, access, vehicle rules, policies, booking instructions, FAQ.
- DYNAMIC_INFORMATION: live availability, stored opening hours, or current pricing. Set
  dynamic_subtype to AVAILABILITY, OPENING_HOURS, or PRICING. For opening hours, extract an
  explicitly named weekday; otherwise leave weekday null.
- RESERVATION: requests to reserve/book a parking space. Do not extract personal details.
- UNSUPPORTED: requests unrelated to parking information or parking reservations.
For non-dynamic routes, dynamic_subtype and weekday must be null."""


class IntentClassificationError(RuntimeError):
    """Raised when the model does not produce a valid intent decision."""


class IntentRouter:
    """Small structured-output classifier around the centrally configured model."""

    def __init__(self, chat_model: Any) -> None:
        self._classifier = chat_model.with_structured_output(
            IntentDecision,
            method="json_schema",
        )

    def classify(self, query: str) -> IntentDecision:
        """Classify a nonblank request and validate the model's structured result."""
        if not query.strip():
            raise ValueError("query must not be blank")
        try:
            raw = self._classifier.invoke(
                [SystemMessage(content=ROUTER_POLICY), HumanMessage(content=query)]
            )
            return raw if isinstance(raw, IntentDecision) else IntentDecision.model_validate(raw)
        except (TypeError, ValueError, ValidationError) as error:
            raise IntentClassificationError("chat model returned an invalid intent") from error
