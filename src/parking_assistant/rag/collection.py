"""Explicit Weaviate schema for approved public parking knowledge."""

from __future__ import annotations

from typing import Any

from weaviate import WeaviateClient
from weaviate.classes.config import Configure, DataType, Property

from parking_assistant.config import Settings, get_settings

PUBLIC_KNOWLEDGE_PROPERTIES = (
    ("chunk_id", DataType.TEXT),
    ("content", DataType.TEXT),
    ("source_document_id", DataType.TEXT),
    ("source", DataType.TEXT),
    ("title", DataType.TEXT),
    ("category", DataType.TEXT),
    ("facility_id", DataType.TEXT),
    ("visibility", DataType.TEXT),
    ("chunk_index", DataType.INT),
)


def collection_schema() -> dict[str, Any]:
    """Build explicit self-vectorized collection arguments for creation/testing."""
    return {
        "description": "Approved public parking information split into retrievable chunks.",
        "vector_config": Configure.Vectors.self_provided(),
        "properties": [
            Property(
                name=name,
                data_type=data_type,
                index_filterable=True,
                index_searchable=data_type is DataType.TEXT,
                skip_vectorization=True,
            )
            for name, data_type in PUBLIC_KNOWLEDGE_PROPERTIES
        ],
    }


def ensure_public_knowledge_collection(
    client: WeaviateClient, settings: Settings | None = None
) -> Any:
    """Create the schema once and return the collection without auto-schema behavior."""
    resolved = settings or get_settings()
    name = resolved.weaviate_knowledge_collection
    if not client.collections.exists(name):
        client.collections.create(name=name, **collection_schema())
    return client.collections.use(name)
