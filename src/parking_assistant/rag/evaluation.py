"""Lightweight retrieval metrics and CLI for the curated Stage 1B dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.config import get_settings
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.retrieval import RetrievalMode, RetrievalOptions, RetrievalService
from parking_assistant.rag.weaviate import connect_weaviate

DEFAULT_DATASET = Path("evaluation/retrieval_dataset.jsonl")


class EvaluationRecord(BaseModel):
    """One manually judged retrieval query."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)
    relevant_document_ids: list[str] = Field(min_length=1)
    category: str = Field(min_length=1)


class EvaluationMetrics(BaseModel):
    """Macro-averaged retrieval quality metrics."""

    model_config = ConfigDict(frozen=True)

    precision_at_k: float
    recall_at_k: float
    mrr: float


class RetrievalEvaluationReport(BaseModel):
    """Comparable results for every native retrieval mode at one K."""

    model_config = ConfigDict(frozen=True)

    dataset_size: int
    k: int
    application_mode: RetrievalMode = RetrievalMode.HYBRID
    results: dict[RetrievalMode, EvaluationMetrics]


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Calculate document-level Precision@K."""
    if k <= 0:
        raise ValueError("k must be positive")
    return len(set(retrieved[:k]) & relevant) / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Calculate document-level Recall@K."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        raise ValueError("relevant ids must not be empty")
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Return the reciprocal rank of the first relevant document within K."""
    if k <= 0:
        raise ValueError("k must be positive")
    for rank, document_id in enumerate(retrieved[:k], 1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def load_evaluation_dataset(path: Path = DEFAULT_DATASET) -> list[EvaluationRecord]:
    """Load and validate newline-delimited curated judgments."""
    return [
        EvaluationRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate(
    service: RetrievalService,
    records: list[EvaluationRecord],
    mode: RetrievalMode,
    k: int,
) -> EvaluationMetrics:
    """Run retrieval and macro-average Precision@K, Recall@K, and MRR."""
    if not records:
        raise ValueError("evaluation dataset must not be empty")
    precisions: list[float] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    for record in records:
        results = service.search(record.query, mode, RetrievalOptions(limit=k))
        retrieved = [result.metadata.source_document_id for result in results]
        relevant = set(record.relevant_document_ids)
        precisions.append(precision_at_k(retrieved, relevant, k))
        recalls.append(recall_at_k(retrieved, relevant, k))
        reciprocal_ranks.append(reciprocal_rank(retrieved, relevant, k))
    count = len(records)
    return EvaluationMetrics(
        precision_at_k=sum(precisions) / count,
        recall_at_k=sum(recalls) / count,
        mrr=sum(reciprocal_ranks) / count,
    )


def evaluate_all_modes(
    service: RetrievalService,
    records: list[EvaluationRecord],
    k: int,
) -> RetrievalEvaluationReport:
    """Evaluate semantic, BM25, and hybrid retrieval with identical judgments and K."""
    return RetrievalEvaluationReport(
        dataset_size=len(records),
        k=k,
        results={mode: evaluate(service, records, mode, k) for mode in RetrievalMode},
    )


def main() -> None:  # pragma: no cover - network CLI exercised manually/in integration
    """Evaluate one retrieval mode against the curated dataset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=list(RetrievalMode), default=RetrievalMode.HYBRID)
    parser.add_argument("--all", action="store_true", help="evaluate all retrieval modes")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    with connect_weaviate(settings) as client:
        service = RetrievalService(
            ensure_public_knowledge_collection(client, settings),
            create_embeddings(settings),
            settings,
        )
        records = load_evaluation_dataset(args.dataset)
        report: BaseModel = (
            evaluate_all_modes(service, records, args.k)
            if args.all
            else evaluate(service, records, RetrievalMode(args.mode), args.k)
        )
    rendered = json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":  # pragma: no cover
    main()
