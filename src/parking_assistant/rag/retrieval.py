"""Typed semantic, BM25, and hybrid retrieval over public knowledge."""

from __future__ import annotations

import argparse
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from weaviate.classes.query import Filter, MetadataQuery

from parking_assistant.config import Settings, get_settings
from parking_assistant.observability import trace_span
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.weaviate import connect_weaviate


class QueryEmbedder(Protocol):
    """Narrow embedding contract used by semantic and hybrid retrieval."""

    def embed_query(self, text: str) -> list[float]: ...


class RetrievalMode(StrEnum):
    SEMANTIC = "semantic"
    BM25 = "bm25"
    HYBRID = "hybrid"


class RetrievalMetadata(BaseModel):
    """Approved metadata returned independently of Weaviate response types."""

    model_config = ConfigDict(frozen=True)

    source_document_id: str
    source: str
    title: str
    category: str
    facility_id: str
    visibility: str
    chunk_index: int


class RetrievalResult(BaseModel):
    """Provider-neutral retrieval result."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    metadata: RetrievalMetadata
    score: float | None = None
    distance: float | None = None


class RetrievalOptions(BaseModel):
    """Whitelisted structured filters and result count."""

    model_config = ConfigDict(frozen=True)

    facility_id: str | None = None
    category: str | None = None
    limit: int | None = Field(default=None, ge=1, le=25)


def build_public_filter(options: RetrievalOptions) -> Any:
    """Always enforce public visibility and add only approved structured filters."""
    result = Filter.by_property("visibility").equal("public")
    if options.facility_id is not None:
        result &= Filter.by_property("facility_id").equal(options.facility_id)
    if options.category is not None:
        result &= Filter.by_property("category").equal(options.category)
    return result


def map_retrieval_object(item: Any) -> RetrievalResult:
    """Map one Weaviate result to an application-owned typed result."""
    properties = item.properties
    provider_metadata = item.metadata
    return RetrievalResult(
        chunk_id=str(properties["chunk_id"]),
        content=str(properties["content"]),
        metadata=RetrievalMetadata(
            source_document_id=str(properties["source_document_id"]),
            source=str(properties["source"]),
            title=str(properties["title"]),
            category=str(properties["category"]),
            facility_id=str(properties["facility_id"]),
            visibility=str(properties["visibility"]),
            chunk_index=int(properties["chunk_index"]),
        ),
        score=getattr(provider_metadata, "score", None),
        distance=getattr(provider_metadata, "distance", None),
    )


class RetrievalService:
    """Small boundary around native Weaviate retrieval modes."""

    def __init__(self, collection: Any, embedder: QueryEmbedder, settings: Settings) -> None:
        self._collection = collection
        self._embedder = embedder
        self._settings = settings

    def search(
        self,
        query: str,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        options: RetrievalOptions | None = None,
    ) -> list[RetrievalResult]:
        """Run one native retrieval mode with mandatory public filtering."""
        if not query.strip():
            raise ValueError("query must not be blank")
        resolved_options = options or RetrievalOptions()
        limit = resolved_options.limit or self._settings.rag_retrieval_limit
        filters = build_public_filter(resolved_options)
        common = {
            "limit": limit,
            "filters": filters,
            "return_metadata": MetadataQuery(score=True, distance=True),
        }
        with trace_span(
            self._settings,
            f"{mode.value}_retrieval",
            run_type="retriever",
            inputs={"query": query},
            metadata={"retrieval_mode": mode.value, "limit": limit},
        ) as run:
            if mode is RetrievalMode.SEMANTIC:
                response = self._collection.query.near_vector(
                    self._embedder.embed_query(query), **common
                )
            elif mode is RetrievalMode.BM25:
                response = self._collection.query.bm25(query, **common)
            elif mode is RetrievalMode.HYBRID:
                response = self._collection.query.hybrid(
                    query,
                    vector=self._embedder.embed_query(query),
                    alpha=self._settings.rag_hybrid_alpha,
                    **common,
                )
            else:
                raise ValueError(f"unsupported retrieval mode: {mode}")
            results = [map_retrieval_object(item) for item in response.objects]
            if run is not None:
                run.add_outputs(
                    {
                        "source_document_ids": [
                            result.metadata.source_document_id for result in results
                        ],
                        "result_count": len(results),
                    }
                )
            return results


def main() -> None:  # pragma: no cover - network CLI exercised manually/in integration
    """Run an ad-hoc retrieval query for verification and inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--mode", choices=list(RetrievalMode), default=RetrievalMode.HYBRID)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--facility-id")
    parser.add_argument("--category")
    args = parser.parse_args()

    settings = get_settings()
    embedder = create_embeddings(settings)
    with connect_weaviate(settings) as client:
        collection = ensure_public_knowledge_collection(client, settings)
        service = RetrievalService(collection, embedder, settings)
        results = service.search(
            args.query,
            RetrievalMode(args.mode),
            RetrievalOptions(
                limit=args.limit,
                facility_id=args.facility_id,
                category=args.category,
            ),
        )
    for rank, result in enumerate(results, 1):
        print(
            f"{rank}. {result.metadata.source_document_id} "
            f"(chunk={result.chunk_id[:12]}, score={result.score}, distance={result.distance})"
        )


if __name__ == "__main__":  # pragma: no cover
    main()
