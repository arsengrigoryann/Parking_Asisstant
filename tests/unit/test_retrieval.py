"""Unit tests for retrieval mapping, filters, and native mode dispatch."""

from types import SimpleNamespace
from typing import Any

import pytest

from parking_assistant.config import Settings
from parking_assistant.rag.retrieval import (
    RetrievalMode,
    RetrievalOptions,
    RetrievalService,
    build_public_filter,
    map_retrieval_object,
)


class FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.1, float(len(text))]


class FakeQuery:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def near_vector(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("semantic", args, kwargs))
        return self.response

    def bm25(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("bm25", args, kwargs))
        return self.response

    def hybrid(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("hybrid", args, kwargs))
        return self.response


def result_object() -> SimpleNamespace:
    return SimpleNamespace(
        properties={
            "chunk_id": "chunk-1",
            "content": "Parking content",
            "source_document_id": "doc-1",
            "source": "doc.md",
            "title": "A title",
            "category": "faq",
            "facility_id": "facility-1",
            "visibility": "public",
            "chunk_index": 2,
        },
        metadata=SimpleNamespace(score=0.8, distance=0.2),
    )


def flatten_filter_targets(filter_value: Any) -> list[str]:
    if hasattr(filter_value, "filters"):
        return [
            target
            for child in filter_value.filters
            for target in flatten_filter_targets(child)
        ]
    return [filter_value.target]


def test_retrieval_result_mapping_is_provider_neutral() -> None:
    result = map_retrieval_object(result_object())

    assert result.chunk_id == "chunk-1"
    assert result.metadata.source_document_id == "doc-1"
    assert result.metadata.chunk_index == 2
    assert result.score == pytest.approx(0.8)
    assert result.distance == pytest.approx(0.2)


def test_retrieval_filters_always_include_public_and_whitelisted_fields() -> None:
    filters = build_public_filter(RetrievalOptions(facility_id="facility-1", category="faq"))

    assert set(flatten_filter_targets(filters)) == {"visibility", "facility_id", "category"}


@pytest.mark.parametrize("mode", list(RetrievalMode))
def test_retrieval_dispatches_native_mode_with_limits_and_filters(mode: RetrievalMode) -> None:
    query = FakeQuery(SimpleNamespace(objects=[result_object()]))
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        rag_hybrid_alpha=0.7,
        _env_file=None,
    )
    service = RetrievalService(SimpleNamespace(query=query), FakeEmbedder(), settings)

    results = service.search("parking rules", mode, RetrievalOptions(limit=3))

    call_mode, _, kwargs = query.calls[0]
    assert call_mode == mode.value
    assert kwargs["limit"] == 3
    assert "visibility" in flatten_filter_targets(kwargs["filters"])
    if mode is RetrievalMode.HYBRID:
        assert kwargs["alpha"] == pytest.approx(0.7)
    assert results[0].metadata.visibility == "public"
