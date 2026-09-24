"""Unit tests for document-level retrieval metrics and dataset validity."""

import pytest

from parking_assistant.rag.evaluation import (
    EvaluationRecord,
    evaluate,
    evaluate_all_modes,
    load_evaluation_dataset,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from parking_assistant.rag.retrieval import RetrievalMetadata, RetrievalMode, RetrievalResult


def test_precision_recall_and_mrr() -> None:
    retrieved = ["wrong", "relevant-a", "relevant-b", "other"]
    relevant = {"relevant-a", "relevant-b"}

    assert precision_at_k(retrieved, relevant, 3) == pytest.approx(2 / 3)
    assert recall_at_k(retrieved, relevant, 3) == pytest.approx(1.0)
    assert reciprocal_rank(retrieved, relevant, 3) == pytest.approx(0.5)


def test_metrics_handle_no_hit_and_validate_inputs() -> None:
    assert precision_at_k(["wrong"], {"relevant"}, 2) == 0.0
    assert recall_at_k(["wrong"], {"relevant"}, 2) == 0.0
    assert reciprocal_rank(["wrong"], {"relevant"}, 2) == 0.0
    with pytest.raises(ValueError, match="positive"):
        precision_at_k([], {"relevant"}, 0)


def test_curated_dataset_has_requested_query_variety() -> None:
    records = load_evaluation_dataset()

    assert 15 <= len(records) <= 20
    assert "ambiguous" in {record.category for record in records}
    assert all(record.relevant_document_ids for record in records)


def test_evaluate_macro_averages_results() -> None:
    class FakeService:
        def search(self, query: str, mode: RetrievalMode, options: object) -> list[RetrievalResult]:
            document_id = "right" if query == "hit" else "wrong"
            return [
                RetrievalResult(
                    chunk_id="chunk",
                    content="content",
                    metadata=RetrievalMetadata(
                        source_document_id=document_id,
                        source="source.md",
                        title="Title",
                        category="faq",
                        facility_id="facility",
                        visibility="public",
                        chunk_index=0,
                    ),
                )
            ]

    records = [
        EvaluationRecord(query="hit", relevant_document_ids=["right"], category="faq"),
        EvaluationRecord(query="miss", relevant_document_ids=["right"], category="faq"),
    ]
    metrics = evaluate(FakeService(), records, RetrievalMode.BM25, 1)  # type: ignore[arg-type]

    assert metrics.precision_at_k == pytest.approx(0.5)
    assert metrics.recall_at_k == pytest.approx(0.5)
    assert metrics.mrr == pytest.approx(0.5)


def test_evaluate_rejects_empty_dataset() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        evaluate(object(), [], RetrievalMode.BM25, 1)  # type: ignore[arg-type]


def test_all_modes_report_uses_same_dataset_and_k() -> None:
    class FakeService:
        def search(self, query: str, mode: RetrievalMode, options: object) -> list[RetrievalResult]:
            return [
                RetrievalResult(
                    chunk_id="chunk",
                    content="content",
                    metadata=RetrievalMetadata(
                        source_document_id="right",
                        source="source.md",
                        title="Title",
                        category="faq",
                        facility_id="facility",
                        visibility="public",
                        chunk_index=0,
                    ),
                )
            ]

    records = [
        EvaluationRecord(query="hit", relevant_document_ids=["right"], category="faq")
    ]

    report = evaluate_all_modes(FakeService(), records, 1)  # type: ignore[arg-type]

    assert report.dataset_size == 1
    assert report.k == 1
    assert report.application_mode is RetrievalMode.HYBRID
    assert set(report.results) == set(RetrievalMode)
    assert all(metrics.recall_at_k == 1.0 for metrics in report.results.values())
