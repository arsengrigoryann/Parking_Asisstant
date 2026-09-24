# Stage 1 presentation screenshot checklist

Use the dedicated `parking-assistant-stage1` LangSmith project and synthetic/public examples
only. Hide browser account details and API credentials before capturing screenshots.

## 1. Static RAG trace

- Command: `$env:LANGSMITH_PROJECT="parking-assistant-stage1"; uv run python -m parking_assistant.evaluation.demo_traces`
- Query to open: `Where is the parking located?`
- Show: `parking_assistant_request` with `intent_routing`, `static_rag`,
  `hybrid_retrieval`, and `grounded_generation`; public source document IDs and result count.
- Why: demonstrates the complete grounded static path and useful nested observability.

## 2. Dynamic PostgreSQL trace

- Same command; open: `How many parking spaces are currently available?`
- Show: `intent_routing` followed by `dynamic_query`, with no retrieval span.
- Why: proves operational data comes from PostgreSQL rather than RAG.

## 3. Privacy-safe trace

- Same command; open: `What is the cancellation policy for Demo User, plate DEMO123?`
- Show: trace input containing `<PERSON>` and `<CAR_NUMBER>`, metadata
  `pii_detected=true`, and entity types; do not show any raw identity/plate value.
- Why: demonstrates that sanitization happens before tracing, routing, and retrieval.

## 4. LangSmith answer-quality experiment

- Command: `uv run python -m parking_assistant.evaluation.answer_quality --upload-langsmith`
- Show: the `parking-assistant-stage1-answer-quality-*` experiment with correctness,
  groundedness, and relevance feedback columns.
- Why: provides reviewable per-example answer-quality evidence.

## 5. Retrieval evaluation

- Command: `uv run python -m parking_assistant.rag.evaluation --all --k 5 --output evaluation/retrieval_report.json`
- Show: the semantic/BM25/hybrid table in `evaluation/stage1_report.md`.
- Why: compares all retrieval modes on the same 18 queries and K.

## 6. Security evaluation

- Command: `uv run python -m parking_assistant.guardrails.evaluation --output evaluation/security_report.json`
- Show: pass/fail totals, PII leakage, block/pass rates, and cross-session leakage count.
- Why: summarizes the deterministic defense-in-depth evaluation.

## 7. CLI conversation

- Command: `uv run python -m parking_assistant.api.cli --interactive`
- Queries: a public location question, one availability question, then the synthetic
  reservation example from the README followed by cancellation.
- Show: grounded sources/dynamic response, collected synthetic details marked not booked, and
  cancellation confirmation.
- Why: demonstrates the Stage 1 user experience without implying approval or persistence.
