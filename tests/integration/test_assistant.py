"""Representative real Stage 1C queries across OpenAI, Weaviate, and PostgreSQL."""

import os

import pytest
from alembic import command
from alembic.config import Config

from parking_assistant.application import create_assistant_service, create_conversation_service
from parking_assistant.config import get_settings
from parking_assistant.db.seed import seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.ingestion import ingest_public_knowledge
from parking_assistant.rag.weaviate import connect_weaviate
from parking_assistant.reservations.models import ReservationStatus
from parking_assistant.routing import DynamicSubtype, IntentRoute

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use configured real services",
    ),
]

TEST_COLLECTION = "PublicParkingKnowledgeAssistantIntegration"


def test_representative_stage_1c_queries_end_to_end() -> None:
    settings = get_settings().model_copy(
        update={
            "weaviate_knowledge_collection": TEST_COLLECTION,
            "langsmith_tracing": False,
        }
    )
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(settings)
    try:
        with session_scope(create_session_factory(engine)) as session:
            seed_data(session)
    finally:
        engine.dispose()

    with connect_weaviate(settings) as client:
        if client.collections.exists(TEST_COLLECTION):
            client.collections.delete(TEST_COLLECTION)
        collection = ensure_public_knowledge_collection(client, settings)
        ingest_public_knowledge(collection, create_embeddings(settings), settings)

    try:
        with create_assistant_service(settings) as assistant:
            location = assistant.answer_query("Where is the parking located?")
            cancellation = assistant.answer_query("What is the cancellation policy?")
            availability = assistant.answer_query(
                "How many parking spaces are currently available?"
            )
            opening = assistant.answer_query("Are you open on Monday?")
            pricing = assistant.answer_query("What does parking cost?")
            reservation = assistant.answer_query("I want to reserve a parking space.")
            unsupported = assistant.answer_query("Explain quantum computing.")

        assert location.route is IntentRoute.STATIC_INFORMATION
        assert location.sources
        assert any(
            source.document_id == "central-station-location-access"
            for source in location.sources
        )
        assert cancellation.route is IntentRoute.STATIC_INFORMATION
        assert any(
            source.document_id == "central-station-cancellation-policy"
            for source in cancellation.sources
        )
        assert availability.dynamic_subtype is DynamicSubtype.AVAILABILITY
        assert "19 of 24" in availability.answer
        assert opening.dynamic_subtype is DynamicSubtype.OPENING_HOURS
        assert "07:00 to 23:00" in opening.answer
        assert pricing.dynamic_subtype is DynamicSubtype.PRICING
        assert "USD 3.50 per hour" in pricing.answer
        assert reservation.route is IntentRoute.RESERVATION
        assert "no personal or vehicle information has been collected" in reservation.answer
        assert unsupported.route is IntentRoute.UNSUPPORTED
        assert unsupported.sources == []
    finally:
        with connect_weaviate(settings) as client:
            client.collections.delete(TEST_COLLECTION)


def test_real_multi_turn_reservation_extraction_and_cancellation() -> None:
    settings = get_settings().model_copy(update={"langsmith_tracing": False})
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(settings)
    try:
        with session_scope(create_session_factory(engine)) as session:
            seed_data(session)
    finally:
        engine.dispose()

    with create_conversation_service(settings) as conversation:
        started = conversation.handle_message(
            "I want to reserve a parking space tomorrow.", "reservation-integration"
        )
        identity = conversation.handle_message(
            "My demo name is Test User and the demo plate is DEMO123.",
            "reservation-integration",
        )
        complete = conversation.handle_message(
            "Tomorrow from 10:00 to 13:00.", "reservation-integration"
        )
        corrected = conversation.handle_message(
            "Actually, change the demo plate to TEST456.", "reservation-integration"
        )
        cancelled = conversation.handle_message(
            "Cancel this reservation.", "reservation-integration"
        )

    assert started.reservation_status is ReservationStatus.COLLECTING
    assert identity.missing_fields
    assert complete.reservation_status is ReservationStatus.COMPLETE
    assert complete.reservation is not None
    assert complete.reservation.first_name == "Test"
    assert complete.reservation.last_name == "User"
    assert complete.reservation.car_number == "DEMO123"
    assert complete.reservation.start_datetime.tzinfo is not None
    assert corrected.reservation is not None
    assert corrected.reservation.car_number == "TEST456"
    assert cancelled.reservation_status is ReservationStatus.CANCELLED
