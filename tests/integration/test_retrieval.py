"""Real Weaviate and OpenAI integration coverage for Stage 1B retrieval."""

import os
from uuid import uuid4

import pytest

from parking_assistant.config import get_settings
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.ingestion import ingest_public_knowledge
from parking_assistant.rag.retrieval import RetrievalMode, RetrievalOptions, RetrievalService
from parking_assistant.rag.weaviate import connect_weaviate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use local services",
    ),
]

TEST_COLLECTION = "PublicParkingKnowledgeIntegration"


def test_collection_ingestion_idempotency_and_all_retrieval_modes() -> None:
    settings = get_settings().model_copy(update={"weaviate_knowledge_collection": TEST_COLLECTION})
    embedder = create_embeddings(settings)

    with connect_weaviate(settings) as client:
        if client.collections.exists(TEST_COLLECTION):
            client.collections.delete(TEST_COLLECTION)
        try:
            collection = ensure_public_knowledge_collection(client, settings)
            first = ingest_public_knowledge(collection, embedder, settings)
            second = ingest_public_knowledge(collection, embedder, settings)

            assert first.documents_loaded == 7
            assert first.inserted == first.chunks_generated
            assert second.inserted == 0
            assert second.skipped == first.chunks_generated

            service = RetrievalService(collection, embedder, settings)
            cases = {
                RetrievalMode.SEMANTIC: "How do I enter the car park?",
                RetrievalMode.BM25: "trailers oversized commercial vehicles",
                RetrievalMode.HYBRID: "How can I cancel my reservation?",
            }
            for mode, query in cases.items():
                results = service.search(query, mode, RetrievalOptions(limit=3))
                assert results
                assert all(result.metadata.visibility == "public" for result in results)

            private_query = "secret internal parking instructions"
            collection.data.insert(
                uuid=uuid4(),
                properties={
                    "chunk_id": "private-test-chunk",
                    "content": private_query,
                    "source_document_id": "private-test-document",
                    "source": "private.md",
                    "title": "Private test",
                    "category": "faq",
                    "facility_id": "test-facility",
                    "visibility": "private",
                    "chunk_index": 0,
                },
                vector=embedder.embed_query(private_query),
            )
            public_only = service.search(private_query, RetrievalMode.HYBRID)
            assert all(result.chunk_id != "private-test-chunk" for result in public_only)

            filtered = service.search(
                "parking",
                RetrievalMode.HYBRID,
                RetrievalOptions(category="location", limit=3),
            )
            assert filtered
            assert all(result.metadata.category == "location" for result in filtered)
        finally:
            client.collections.delete(TEST_COLLECTION)
