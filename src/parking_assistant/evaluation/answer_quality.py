"""Generated static-answer quality evaluation with optional LangSmith publishing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import Client
from langsmith.evaluation import evaluate as langsmith_evaluate
from langsmith.schemas import Example, Run
from pydantic import BaseModel, ConfigDict, Field

from parking_assistant.chat import create_chat_model
from parking_assistant.config import Settings, get_settings
from parking_assistant.rag.answering import GroundedRAGService
from parking_assistant.rag.collection import ensure_public_knowledge_collection
from parking_assistant.rag.embeddings import create_embeddings
from parking_assistant.rag.retrieval import RetrievalService
from parking_assistant.rag.weaviate import connect_weaviate

DEFAULT_DATASET = Path("evaluation/answer_quality_dataset.jsonl")
DEFAULT_OUTPUT = Path("evaluation/answer_quality_report.json")
DEFAULT_MARKDOWN = Path("evaluation/answer_quality_report.md")
LANGSMITH_DATASET = "parking-assistant-stage1-answer-quality-v1"

JUDGE_POLICY = """Evaluate one answer to a public parking-information question.
Use only the supplied reference facts. Score each dimension from 0.0 to 1.0:
- correctness: claims agree with the facts and omit material factual errors;
- groundedness: factual claims are supported by the supplied facts;
- relevance: the answer directly addresses the question without distracting content.
Be strict, concise, and return only the requested structured result."""


class AnswerQualityCase(BaseModel):
    """One public, stable answer-quality judgment case."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reference_facts: list[str] = Field(min_length=1)
    expected_document_ids: list[str] = Field(min_length=1)


class AnswerQualityJudgment(BaseModel):
    """Structured LLM-as-judge scores constrained to the documented rubric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correctness: float = Field(ge=0.0, le=1.0)
    groundedness: float = Field(ge=0.0, le=1.0)
    relevance: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=500)


class AnswerQualityResult(BaseModel):
    """Measured output and judgment for one case."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    question: str
    answer: str
    source_document_ids: list[str]
    judgment: AnswerQualityJudgment


class AnswerQualityReport(BaseModel):
    """Aggregate answer-quality report from actually executed cases."""

    model_config = ConfigDict(frozen=True)

    dataset_size: int
    completed: int
    failures: int
    average_correctness: float
    average_groundedness: float
    average_relevance: float
    results: list[AnswerQualityResult]
    failed_case_ids: list[str] = Field(default_factory=list)
    langsmith_experiment: str | None = None


def load_answer_quality_dataset(path: Path = DEFAULT_DATASET) -> list[AnswerQualityCase]:
    """Load the synthetic-free public static-answer dataset."""
    return [
        AnswerQualityCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class AnswerQualityJudge:
    """One structured evaluator around the centrally configured chat model."""

    def __init__(self, chat_model: Any) -> None:
        self._judge = chat_model.with_structured_output(
            AnswerQualityJudgment,
            method="json_schema",
        )

    def judge(self, case: AnswerQualityCase, answer: str) -> AnswerQualityJudgment:
        facts = "\n".join(f"- {fact}" for fact in case.reference_facts)
        raw = self._judge.invoke(
            [
                SystemMessage(content=JUDGE_POLICY),
                HumanMessage(
                    content=(
                        f"Question: {case.question}\n\n"
                        f"Reference facts:\n{facts}\n\n"
                        f"Answer to evaluate:\n{answer}"
                    )
                ),
            ]
        )
        return (
            raw
            if isinstance(raw, AnswerQualityJudgment)
            else AnswerQualityJudgment.model_validate(raw)
        )


def _aggregate(
    cases: list[AnswerQualityCase],
    results: list[AnswerQualityResult],
    failed_case_ids: list[str],
) -> AnswerQualityReport:
    if not cases:
        raise ValueError("answer-quality dataset must not be empty")
    completed = len(results)
    if completed == 0:
        return AnswerQualityReport(
            dataset_size=len(cases),
            completed=0,
            failures=len(failed_case_ids),
            average_correctness=0.0,
            average_groundedness=0.0,
            average_relevance=0.0,
            results=[],
            failed_case_ids=failed_case_ids,
        )
    return AnswerQualityReport(
        dataset_size=len(cases),
        completed=completed,
        failures=len(failed_case_ids),
        average_correctness=sum(item.judgment.correctness for item in results) / completed,
        average_groundedness=sum(item.judgment.groundedness for item in results) / completed,
        average_relevance=sum(item.judgment.relevance for item in results) / completed,
        results=results,
        failed_case_ids=failed_case_ids,
    )


def evaluate_answer_quality(
    cases: list[AnswerQualityCase],
    settings: Settings,
) -> AnswerQualityReport:  # pragma: no cover - exercised by the real evaluation CLI
    """Generate and judge answers against stable public reference facts."""
    if not cases:
        raise ValueError("answer-quality dataset must not be empty")
    evaluation_settings = settings.model_copy(update={"langsmith_tracing": False})
    chat_model = create_chat_model(evaluation_settings)
    judge = AnswerQualityJudge(chat_model)
    results: list[AnswerQualityResult] = []
    failures: list[str] = []
    with connect_weaviate(evaluation_settings) as client:
        retrieval = RetrievalService(
            ensure_public_knowledge_collection(client, evaluation_settings),
            create_embeddings(evaluation_settings),
            evaluation_settings,
        )
        answerer = GroundedRAGService(retrieval, chat_model, evaluation_settings)
        for case in cases:
            try:
                generated = answerer.answer(case.question)
                results.append(
                    AnswerQualityResult(
                        case_id=case.case_id,
                        question=case.question,
                        answer=generated.answer,
                        source_document_ids=[
                            source.document_id for source in generated.sources
                        ],
                        judgment=judge.judge(case, generated.answer),
                    )
                )
            except Exception:
                failures.append(case.case_id)
    return _aggregate(cases, results, failures)


def publish_langsmith_experiment(
    report: AnswerQualityReport,
    cases: list[AnswerQualityCase],
    settings: Settings,
) -> str:  # pragma: no cover - exercised against the configured LangSmith service
    """Publish measured outputs and feedback as a reproducible offline experiment."""
    if settings.langsmith_api_key is None:
        raise ValueError("LANGSMITH_API_KEY is required to publish the experiment")
    client = Client(
        api_url=str(settings.langsmith_endpoint),
        api_key=settings.langsmith_api_key.get_secret_value(),
    )
    if not client.has_dataset(dataset_name=LANGSMITH_DATASET):
        client.create_dataset(
            LANGSMITH_DATASET,
            description="Stable public Stage 1 parking answer-quality cases.",
        )
        client.create_examples(
            dataset_name=LANGSMITH_DATASET,
            examples=[
                {
                    "inputs": {"question": case.question},
                    "outputs": {
                        "reference_facts": case.reference_facts,
                        "expected_document_ids": case.expected_document_ids,
                    },
                }
                for case in cases
            ],
        )
    measured = {item.question: item for item in report.results}

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        result = measured[str(inputs["question"])]
        return {
            "answer": result.answer,
            "source_document_ids": result.source_document_ids,
        }

    def evaluator(run: Run, example: Example | None) -> dict[str, Any]:
        if example is None or example.inputs is None:
            raise ValueError("answer-quality evaluation requires a dataset example")
        question = str(example.inputs["question"])
        judgment = measured[question].judgment
        return {
            "results": [
                {"key": "correctness", "score": judgment.correctness},
                {"key": "groundedness", "score": judgment.groundedness},
                {"key": "relevance", "score": judgment.relevance},
            ]
        }

    experiment = langsmith_evaluate(
        target,
        data=LANGSMITH_DATASET,
        evaluators=[evaluator],
        experiment_prefix="parking-assistant-stage1-answer-quality",
        description="Stage 1 public static-answer correctness, groundedness, and relevance.",
        metadata={"stage": "1", "contains_pii": False},
        max_concurrency=0,
        client=client,
    )
    list(experiment)
    client.close(timeout=5)
    return experiment.experiment_name


def render_markdown(report: AnswerQualityReport) -> str:
    """Render a concise human-readable report from measured JSON data."""
    experiment = report.langsmith_experiment or "not published"
    return (
        "# Stage 1 answer-quality evaluation\n\n"
        f"- Dataset cases: {report.dataset_size}\n"
        f"- Completed: {report.completed}\n"
        f"- Failures: {report.failures}\n"
        f"- Average correctness: {report.average_correctness:.3f}\n"
        f"- Average groundedness: {report.average_groundedness:.3f}\n"
        f"- Average relevance: {report.average_relevance:.3f}\n"
        f"- LangSmith experiment: `{experiment}`\n"
    )


def main() -> None:  # pragma: no cover - real network evaluation CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--upload-langsmith", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    cases = load_answer_quality_dataset(args.dataset)
    report = evaluate_answer_quality(cases, settings)
    if args.upload_langsmith and report.failures == 0:
        experiment = publish_langsmith_experiment(report, cases, settings)
        report = report.model_copy(update={"langsmith_experiment": experiment})
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report.model_dump(mode="json", exclude={"results"}), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
