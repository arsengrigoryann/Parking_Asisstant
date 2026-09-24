"""Idempotent ingestion CLI for approved public parking documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from parking_assistant.config import Settings, get_settings
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.documents import chunk_documents, load_public_documents
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.weaviate import connect_weaviate


class DocumentEmbedder(Protocol):
    """Narrow embedding contract used by ingestion."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class IngestionSummary:
    """Observable counts from one ingestion run."""

    documents_loaded: int
    chunks_generated: int
    inserted: int
    skipped: int


def object_uuid(chunk_id: str) -> UUID:
    """Map stable chunk identity to stable Weaviate object identity."""
    return uuid5(NAMESPACE_URL, f"parking-assistant/public-knowledge/{chunk_id}")


def ingest_public_knowledge(
    collection: Any,
    embedder: DocumentEmbedder,
    settings: Settings,
) -> IngestionSummary:
    """Validate, chunk, embed missing objects, and insert without deleting any data."""
    documents = load_public_documents()
    chunks = chunk_documents(
        documents,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    missing = [chunk for chunk in chunks if not collection.data.exists(object_uuid(chunk.chunk_id))]
    vectors = embedder.embed_documents([chunk.content for chunk in missing]) if missing else []
    if len(vectors) != len(missing):
        raise ValueError("embedding provider returned an unexpected number of vectors")
    for chunk, vector in zip(missing, vectors, strict=True):
        collection.data.insert(
            uuid=object_uuid(chunk.chunk_id),
            properties=chunk.properties(),
            vector=vector,
        )
    return IngestionSummary(
        documents_loaded=len(documents),
        chunks_generated=len(chunks),
        inserted=len(missing),
        skipped=len(chunks) - len(missing),
    )


def main() -> None:  # pragma: no cover - network CLI exercised by integration tests
    """Ingest the configured corpus and print a concise deterministic summary."""
    settings = get_settings()
    embedder = create_embeddings(settings)
    with connect_weaviate(settings) as client:
        collection = ensure_public_knowledge_collection(client, settings)
        summary = ingest_public_knowledge(collection, embedder, settings)
    print(f"Documents loaded: {summary.documents_loaded}")
    print(f"Chunks generated: {summary.chunks_generated}")
    print(f"Inserted: {summary.inserted}")
    print(f"Updated/skipped: {summary.skipped}")


if __name__ == "__main__":  # pragma: no cover
    main()
