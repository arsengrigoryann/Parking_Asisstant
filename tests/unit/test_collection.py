"""Unit tests for the explicit Weaviate public knowledge schema."""

from typing import Any

from weaviate.classes.config import DataType

from parking_assistant.config import Settings
from parking_assistant.rag.collection import (
    PUBLIC_KNOWLEDGE_PROPERTIES,
    collection_schema,
    ensure_public_knowledge_collection,
)


class FakeCollections:
    def __init__(self, exists: bool) -> None:
        self._exists = exists
        self.created: list[dict[str, Any]] = []

    def exists(self, name: str) -> bool:
        return self._exists

    def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def use(self, name: str) -> str:
        return name


class FakeClient:
    def __init__(self, exists: bool) -> None:
        self.collections = FakeCollections(exists)


def settings() -> Settings:
    return Settings(database_url="postgresql://x:x@localhost/x", _env_file=None)


def test_collection_schema_has_all_explicit_self_vectorized_properties() -> None:
    schema = collection_schema()
    expected = dict(PUBLIC_KNOWLEDGE_PROPERTIES)
    configured = {prop.name: prop.dataType for prop in schema["properties"]}

    assert configured == expected
    assert expected["content"] is DataType.TEXT
    assert expected["chunk_index"] is DataType.INT
    assert schema["vector_config"].vectorizer.vectorizer.value == "none"


def test_collection_creation_is_idempotent() -> None:
    missing_client = FakeClient(exists=False)
    existing_client = FakeClient(exists=True)

    created = ensure_public_knowledge_collection(missing_client, settings())  # type: ignore[arg-type]
    assert created == "PublicParkingKnowledge"
    assert len(missing_client.collections.created) == 1
    assert missing_client.collections.created[0]["name"] == "PublicParkingKnowledge"

    reused = ensure_public_knowledge_collection(existing_client, settings())  # type: ignore[arg-type]
    assert reused == "PublicParkingKnowledge"
    assert existing_client.collections.created == []
