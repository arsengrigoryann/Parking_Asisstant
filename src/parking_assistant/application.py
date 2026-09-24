"""Application-level Stage 1C routing without LangGraph or autonomous agents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.models import PricingUnit, Weekday
from parking_assistant.db.queries import (
    AvailabilitySnapshot,
    DynamicParkingService,
    OpeningHoursResult,
    PricingResult,
)
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.guardrails.privacy import PrivacyService
from parking_assistant.guardrails.security import (
    SECURITY_REFUSAL_MESSAGE,
    OutputGuardrail,
    is_data_exfiltration_request,
)
from parking_assistant.observability import assistant_trace, trace_span
from parking_assistant.rag.answering import AnswerSource, GroundedAnswer, GroundedRAGService
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.retrieval import RetrievalService
from parking_assistant.rag.weaviate import connect_weaviate
from parking_assistant.reservations.extraction import ReservationExtractor
from parking_assistant.reservations.models import (
    ReservationDetails,
    ReservationField,
    ReservationStatus,
    ReservationTurnResult,
)
from parking_assistant.reservations.service import (
    ReservationCollectionService,
    is_explicit_reservation_start,
    is_reservation_cancellation,
)
from parking_assistant.routing import DynamicSubtype, IntentDecision, IntentRoute, IntentRouter

RESERVATION_BOUNDARY_MESSAGE = (
    "This is a reservation request. Reservation detail collection is not available yet, "
    "so no personal or vehicle information has been collected."
)
UNSUPPORTED_MESSAGE = (
    "I can help with parking information and parking reservation requests, but not that topic."
)


class AssistantResponse(BaseModel):
    """Typed response shared by every route."""

    model_config = ConfigDict(frozen=True)

    answer: str
    route: IntentRoute
    dynamic_subtype: DynamicSubtype | None = None
    sources: list[AnswerSource] = Field(default_factory=list)
    reservation_status: ReservationStatus | None = None
    missing_fields: list[ReservationField] = Field(default_factory=list)
    validation_errors: dict[ReservationField, str] = Field(default_factory=dict)
    reservation: ReservationDetails | None = None


class IntentClassifier(Protocol):
    def classify(self, query: str) -> IntentDecision: ...


class StaticAnswerer(Protocol):
    def answer(self, query: str) -> GroundedAnswer: ...


class DynamicReader(Protocol):
    def availability(self) -> AvailabilitySnapshot: ...

    def opening_hours(self, weekday: Weekday) -> OpeningHoursResult | None: ...

    def pricing(self) -> list[PricingResult]: ...


class LazyStaticAnswerer:
    """Open Weaviate only when the classified route actually needs static RAG."""

    def __init__(self, settings: Settings, chat_model: object) -> None:
        self._settings = settings
        self._chat_model = chat_model
        self._embedder = create_embeddings(settings)

    def answer(self, query: str) -> GroundedAnswer:
        with connect_weaviate(self._settings) as client:
            retrieval = RetrievalService(
                ensure_public_knowledge_collection(client, self._settings),
                self._embedder,
                self._settings,
            )
            return GroundedRAGService(retrieval, self._chat_model, self._settings).answer(query)


def _format_price(rule: PricingResult) -> str:
    label = (
        "General"
        if rule.space_type is None
        else rule.space_type.value.replace("_", " ").title()
    )
    unit = {
        PricingUnit.PER_HOUR: "per hour",
        PricingUnit.DAILY_MAXIMUM: "daily maximum",
    }[rule.unit]
    return f"{label}: {rule.currency} {rule.amount:.2f} {unit}"


class AssistantService:
    """Coordinate classification and one bounded route per request."""

    def __init__(
        self,
        router: IntentClassifier,
        static_answerer: StaticAnswerer,
        dynamic_reader: DynamicReader,
        settings: Settings,
        privacy: PrivacyService | None = None,
    ) -> None:
        self._router = router
        self._static_answerer = static_answerer
        self._dynamic = dynamic_reader
        self._settings = settings
        self._privacy = privacy or PrivacyService(settings.reservation_car_number_pattern)
        self._output_guardrail = OutputGuardrail(self._privacy)

    def answer_query(self, query: str) -> AssistantResponse:
        """Classify once, execute only the selected route, and return a typed response."""
        if not query.strip():
            raise ValueError("query must not be blank")
        if is_data_exfiltration_request(query):
            return self._protect_general_output(
                AssistantResponse(
                    answer=SECURITY_REFUSAL_MESSAGE,
                    route=IntentRoute.UNSUPPORTED,
                )
            )
        privacy = self._privacy.inspect_and_redact(query)
        safe_query = privacy.sanitized_text
        with assistant_trace(
            self._settings,
            safe_query,
            pii_detected=privacy.pii_detected,
            entity_types=privacy.entity_types,
        ) as run:
            with trace_span(
                self._settings,
                "intent_routing",
                run_type="chain",
                inputs={"query": safe_query},
            ) as routing_run:
                decision = self._router.classify(safe_query)
                if routing_run is not None:
                    routing_run.add_outputs(decision.model_dump(mode="json"))
            response = self._answer_classified(safe_query, decision)
            response = self._protect_general_output(response)
            if run is not None:
                run.add_metadata({"route": response.route.value})
                run.add_outputs(response.model_dump(mode="json"))
            return response

    def _protect_general_output(self, response: AssistantResponse) -> AssistantResponse:
        inspected = self._output_guardrail.general(response.answer)
        return response.model_copy(update={"answer": inspected.sanitized_text})

    def _answer_classified(self, query: str, decision: IntentDecision) -> AssistantResponse:
        if decision.route is IntentRoute.STATIC_INFORMATION:
            with trace_span(
                self._settings,
                "static_rag",
                run_type="chain",
                inputs={"query": query},
            ) as static_run:
                grounded = self._static_answerer.answer(query)
                if static_run is not None:
                    static_run.add_outputs(
                        {
                            "source_document_ids": [
                                source.document_id for source in grounded.sources
                            ],
                            "result_count": len(grounded.sources),
                        }
                    )
            return AssistantResponse(
                answer=grounded.answer,
                route=decision.route,
                sources=grounded.sources,
            )
        if decision.route is IntentRoute.RESERVATION:
            return AssistantResponse(answer=RESERVATION_BOUNDARY_MESSAGE, route=decision.route)
        if decision.route is IntentRoute.UNSUPPORTED:
            return AssistantResponse(answer=UNSUPPORTED_MESSAGE, route=decision.route)
        subtype = decision.dynamic_subtype
        if subtype is None:
            raise ValueError("dynamic route requires a supported subtype")
        with trace_span(
            self._settings,
            "dynamic_query",
            run_type="tool",
            metadata={"dynamic_subtype": subtype.value},
        ) as dynamic_run:
            response = self._answer_dynamic(decision)
            if dynamic_run is not None:
                dynamic_run.add_outputs({"dynamic_subtype": subtype.value})
            return response

    def _answer_dynamic(self, decision: IntentDecision) -> AssistantResponse:
        subtype = decision.dynamic_subtype
        if subtype is DynamicSubtype.AVAILABILITY:
            snapshot = self._dynamic.availability()
            breakdown = ", ".join(
                f"{item.space_type.value}: {item.available}/{item.total_active}"
                for item in snapshot.by_type
            )
            answer = (
                f"{snapshot.available} of {snapshot.total_active} active parking spaces are "
                f"currently available. By type: {breakdown}."
            )
        elif subtype is DynamicSubtype.OPENING_HOURS:
            if decision.weekday is None:
                answer = "Please specify a weekday so I can check the stored opening hours."
            else:
                hours = self._dynamic.opening_hours(decision.weekday)
                if hours is None:
                    answer = f"Opening hours for {decision.weekday.value.title()} are unavailable."
                elif hours.is_closed:
                    answer = f"The parking facility is closed on {decision.weekday.value.title()}."
                else:
                    assert hours.opens_at is not None and hours.closes_at is not None
                    answer = (
                        f"On {decision.weekday.value.title()}, the parking facility is open "
                        f"from {hours.opens_at:%H:%M} to {hours.closes_at:%H:%M}."
                    )
        elif subtype is DynamicSubtype.PRICING:
            rules = self._dynamic.pricing()
            answer = (
                "Current stored pricing: " + "; ".join(_format_price(rule) for rule in rules) + "."
                if rules
                else "Current pricing information is unavailable."
            )
        else:  # schema validation makes this unreachable for a valid router
            raise ValueError("dynamic route requires a supported subtype")
        return AssistantResponse(
            answer=answer,
            route=IntentRoute.DYNAMIC_INFORMATION,
            dynamic_subtype=subtype,
        )


class ConversationService:
    """Add session-scoped reservation collection around the Stage 1C service."""

    def __init__(
        self,
        assistant: AssistantService,
        reservations: ReservationCollectionService,
        output_guardrail: OutputGuardrail | None = None,
    ) -> None:
        self._assistant = assistant
        self._reservations = reservations
        self._output_guardrail = output_guardrail

    def handle_message(self, message: str, session_id: str) -> AssistantResponse:
        """Continue active reservations deterministically; otherwise use normal routing."""
        active = self._reservations.has_session(session_id)
        if active and is_reservation_cancellation(message):
            return self._reservation_response(self._reservations.cancel(session_id))
        if is_data_exfiltration_request(message):
            response = AssistantResponse(
                answer=SECURITY_REFUSAL_MESSAGE,
                route=IntentRoute.UNSUPPORTED,
            )
            if self._output_guardrail is None:
                return response
            inspected = self._output_guardrail.general(response.answer)
            return response.model_copy(update={"answer": inspected.sanitized_text})
        if active or is_explicit_reservation_start(message):
            return self._reservation_response(self._reservations.collect(session_id, message))

        response = self._assistant.answer_query(message)
        if response.route is IntentRoute.RESERVATION:
            return self._reservation_response(self._reservations.collect(session_id, message))
        return response

    def _reservation_response(self, result: ReservationTurnResult) -> AssistantResponse:
        response = _reservation_response(result)
        if self._output_guardrail is None:
            return response
        inspected = self._output_guardrail.reservation(response.answer, result.reservation)
        return response.model_copy(update={"answer": inspected.sanitized_text})


def _reservation_response(result: ReservationTurnResult) -> AssistantResponse:
    return AssistantResponse(
        answer=result.answer,
        route=IntentRoute.RESERVATION,
        reservation_status=result.status,
        missing_fields=result.missing_fields,
        validation_errors=result.validation_errors,
        reservation=result.reservation,
    )


@contextmanager
def create_assistant_service(settings: Settings | None = None) -> Iterator[AssistantService]:
    """Compose Stage 1C dependencies and dispose the SQLAlchemy engine afterward."""
    resolved = settings or get_settings()
    chat_model = create_chat_model(resolved)
    engine = create_database_engine(resolved)
    try:
        privacy = PrivacyService(resolved.reservation_car_number_pattern)
        yield AssistantService(
            router=IntentRouter(chat_model),
            static_answerer=LazyStaticAnswerer(resolved, chat_model),
            dynamic_reader=DynamicParkingService(create_session_factory(engine)),
            settings=resolved,
            privacy=privacy,
        )
    finally:
        engine.dispose()


@contextmanager
def create_conversation_service(
    settings: Settings | None = None,
) -> Iterator[ConversationService]:
    """Compose a session-scoped assistant with ephemeral reservation state."""
    resolved = settings or get_settings()
    chat_model = create_chat_model(resolved)
    engine = create_database_engine(resolved)
    try:
        privacy = PrivacyService(resolved.reservation_car_number_pattern)
        dynamic = DynamicParkingService(create_session_factory(engine))
        assistant = AssistantService(
            router=IntentRouter(chat_model),
            static_answerer=LazyStaticAnswerer(resolved, chat_model),
            dynamic_reader=dynamic,
            settings=resolved,
            privacy=privacy,
        )
        reservations = ReservationCollectionService(
            extractor=ReservationExtractor(chat_model),
            settings=resolved,
            timezone_provider=dynamic.facility_timezone,
        )
        yield ConversationService(assistant, reservations, OutputGuardrail(privacy))
    finally:
        engine.dispose()


def answer_query(query: str) -> AssistantResponse:
    """Convenience entry point for one independently scoped assistant request."""
    with create_assistant_service() as service:
        return service.answer_query(query)
