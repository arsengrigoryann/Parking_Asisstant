"""Unit tests for idempotent application-vector ingestion."""

from typing import Any

import pytest

from parking_assistant.config import Settings
from parking_assistant.rag.ingestion import ingest_public_knowledge, object_uuid


class FakeData:
    def __init__(self, existing: set[object] | None = None) -> None:
        self.existing = existing or set()
        self.inserted: list[dict[str, Any]] = []

    def exists(self, uuid: object) -> bool:
        return uuid in self.existing

    def insert(self, **kwargs: Any) -> None:
        self.inserted.append(kwargs)
        self.existing.add(kwargs["uuid"])


class FakeEmbedder:
    def __init__(self, mismatch: bool = False) -> None:
        self.inputs: list[str] = []
        self.mismatch = mismatch

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        if self.mismatch:
            return []
        return [[float(index), 0.5] for index, _ in enumerate(texts)]


def settings() -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        rag_chunk_size=900,
        rag_chunk_overlap=120,
        _env_file=None,
    )


def test_ingestion_inserts_vectors_then_skips_stable_objects() -> None:
    data = FakeData()
    collection = type("Collection", (), {"data": data})()
    embedder = FakeEmbedder()

    first = ingest_public_knowledge(collection, embedder, settings())
    second = ingest_public_knowledge(collection, embedder, settings())

    assert first.documents_loaded == 7
    assert first.chunks_generated == 7
    assert first.inserted == 7
    assert first.skipped == 0
    assert second.inserted == 0
    assert second.skipped == 7
    assert len(data.inserted) == 7
    assert all(record["vector"] for record in data.inserted)
    assert len(set(record["uuid"] for record in data.inserted)) == 7


def test_ingestion_rejects_embedding_count_mismatch() -> None:
    collection = type("Collection", (), {"data": FakeData()})()

    with pytest.raises(ValueError, match="unexpected number"):
        ingest_public_knowledge(collection, FakeEmbedder(mismatch=True), settings())


def test_object_uuid_is_stable_and_namespaced() -> None:
    assert object_uuid("same") == object_uuid("same")
    assert object_uuid("same") != object_uuid("different")
