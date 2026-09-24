"""Tests for answer-quality dataset validation and aggregation."""

from parking_assistant.evaluation.answer_quality import (
    AnswerQualityJudge,
    AnswerQualityJudgment,
    AnswerQualityResult,
    _aggregate,
    load_answer_quality_dataset,
    render_markdown,
)
from parking_assistant.evaluation.demo_traces import DEMO_QUERIES


def test_public_answer_quality_dataset_has_expected_variety() -> None:
    cases = load_answer_quality_dataset()

    assert 10 <= len(cases) <= 15
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.reference_facts for case in cases)
    assert all(case.expected_document_ids for case in cases)


def test_answer_quality_aggregation_uses_executed_judgments() -> None:
    case = load_answer_quality_dataset()[0]
    result = AnswerQualityResult(
        case_id=case.case_id,
        question=case.question,
        answer="Measured answer",
        source_document_ids=case.expected_document_ids,
        judgment=AnswerQualityJudgment(
            correctness=0.8,
            groundedness=0.9,
            relevance=1.0,
            rationale="Test judgment.",
        ),
    )

    report = _aggregate([case], [result], [])

    assert report.average_correctness == 0.8
    assert report.average_groundedness == 0.9
    assert report.average_relevance == 1.0
    assert "Average correctness: 0.800" in render_markdown(report)


def test_structured_judge_uses_public_facts_and_validates_output() -> None:
    class Structured:
        def __init__(self) -> None:
            self.messages: object = None

        def invoke(self, messages: object) -> dict[str, object]:
            self.messages = messages
            return {
                "correctness": 1.0,
                "groundedness": 0.9,
                "relevance": 0.8,
                "rationale": "Supported by the supplied facts.",
            }

    class Chat:
        def __init__(self, structured: Structured) -> None:
            self.structured = structured

        def with_structured_output(self, schema: object, method: str) -> Structured:
            return self.structured

    case = load_answer_quality_dataset()[0]
    structured = Structured()

    judgment = AnswerQualityJudge(Chat(structured)).judge(case, "Public answer")

    assert judgment.correctness == 1.0
    assert judgment.groundedness == 0.9
    assert structured.messages is not None


def test_demo_trace_queries_are_bounded_and_use_only_synthetic_pii() -> None:
    assert len(DEMO_QUERIES) == 7
    assert "Demo User" in DEMO_QUERIES[-1]
    assert "DEMO123" in DEMO_QUERIES[-1]
