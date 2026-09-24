# Final Stage 1 Handoff

## 1. Final Stage 1 architecture

Stage 1 is a bounded user assistant with four application outcomes:

- Public static information uses application-generated OpenAI embeddings, a mandatory
  `visibility=public` Weaviate filter, hybrid retrieval, and grounded answer generation.
- Dynamic availability, opening hours, and pricing use deterministic SQLAlchemy queries against
  PostgreSQL. Operational facts are not duplicated into RAG.
- Reservation turns use trace-disabled structured extraction, deterministic validation, and a
  lock-protected in-memory draft keyed by `session_id`.
- Unsupported and explicit exfiltration requests return bounded deterministic responses without
  retrieval or business-data access.

One Presidio privacy service sanitizes normal input before routing, tracing, embeddings, and RAG,
and inspects all output. Reservation extraction is the narrow exception: raw current-turn details
go only to the trace-disabled extractor and never to Weaviate, embeddings, general traces, logs,
or persistence. There is no availability-conflict decision, reservation persistence,
administrator approval, LangGraph, MCP, or booking side effect.

## 2. Safe LangSmith observability

Normal traffic now produces meaningful nested spans when `LANGSMITH_TRACING=true`:

- `parking_assistant_request`
- `intent_routing`
- `static_rag`
- `semantic_retrieval`, `bm25_retrieval`, or `hybrid_retrieval`
- `grounded_generation`
- `dynamic_query`

Inputs are sanitized before the root trace. Safe metadata/output contains route, dynamic subtype,
retrieval mode, public source document IDs, result count, `pii_detected`, and entity types. Raw
detected values, secrets, reservation state, names, plates, emails, and phone numbers are not
added. Reservation extraction remains enclosed by `tracing_context(enabled=False)`.

The verified presentation project is `parking-assistant-stage1`. The safe demo script was run
successfully with public examples plus one synthetic PII query whose trace input contains
`<PERSON>` and `<CAR_NUMBER>`, not the raw values.

A post-run LangSmith audit inspected 100 serialized runs in that project for the exact synthetic
raw name and plate used by the demo and found zero occurrences.

## 3. Retrieval evaluation results

The existing 18-query dataset was executed at K=5 for all modes without parameter retuning:

| Mode | Precision@5 | Recall@5 | MRR |
|---|---:|---:|---:|
| Semantic | 0.244 | 1.000 | 0.755 |
| BM25 | 0.222 | 0.944 | 0.713 |
| Hybrid | 0.244 | 1.000 | 0.807 |

The application continues to use hybrid retrieval. It matched semantic Recall@5 and
Precision@5 while producing the highest measured MRR, and combines semantic and exact-term
signals under the same public-only filter. Raw results are in
`evaluation/retrieval_report.json`.

## 4. Answer-quality evaluation results

`evaluation/answer_quality_dataset.jsonl` contains 12 stable public-information questions with
reference facts and expected public source IDs. Generated answers were judged through typed
structured output against the supplied facts:

- Completed: 12/12
- Failures: 0
- Average correctness: 1.000
- Average groundedness: 1.000
- Average relevance: 1.000

The executed results are in `evaluation/answer_quality_report.json` and `.md`. They were also
published as the LangSmith offline experiment
`parking-assistant-stage1-answer-quality-e305de81`. These are model-judged scores from a small
curated dataset, not a formal proof of answer quality.

## 5. Performance results

The reproducible benchmark used three samples per operation with tracing disabled:

| Operation | Average ms | p50 ms | p95 ms | Failures |
|---|---:|---:|---:|---:|
| Hybrid retrieval | 411.05 | 207.39 | 773.92 | 0 |
| Static RAG request | 1634.65 | 1552.95 | 1868.79 | 0 |
| Dynamic PostgreSQL request | 1694.83 | 4.55 | 4569.31 | 0 |
| Intent classification | 1285.34 | 906.91 | 1986.00 | 0 |
| Complete assistant request | 873.72 | 865.38 | 1005.33 | 0 |

The PostgreSQL average/p95 includes a first-use connection outlier; its median is 4.55 ms.
OpenAI and Weaviate measurements are environment/network-dependent baselines, not deterministic
service-level guarantees. Machine-readable and Markdown reports are in `evaluation/`.

## 6. Security results

The unchanged 20-case synthetic security dataset was re-executed:

- PII leakage rate: 0.000
- Malicious-request block rate: 1.000
- Benign-request pass rate: 1.000
- Cross-session leakage count: 0
- Passed: 20/20; failed: 0

The weekday output phrase `On Monday` was added to the explicit public allowlist after the trace
demo exposed a non-leaking false positive, with a regression test. No security case or expected
result was weakened.

## 7. Unified report and evidence

- Concise report: `evaluation/stage1_report.md`
- Machine-readable report: `evaluation/stage1_report.json`
- Underlying retrieval, answer-quality, performance, security, and verification JSON files:
  `evaluation/`
- Screenshot plan: `docs/stage1_screenshot_checklist.md`
- Final architecture/setup/evaluation documentation: `README.md`

## 8. Exact demo and evaluation commands

```powershell
# Safe presentation traces
$env:LANGSMITH_TRACING = "true"
$env:LANGSMITH_PROJECT = "parking-assistant-stage1"
uv run python -m parking_assistant.evaluation.demo_traces

# Retrieval comparison
$env:LANGSMITH_TRACING = "false"
uv run python -m parking_assistant.rag.evaluation --all --k 5 `
  --output evaluation/retrieval_report.json

# Generated-answer evaluation and LangSmith experiment
uv run python -m parking_assistant.evaluation.answer_quality --upload-langsmith

# Performance and security
uv run python -m parking_assistant.evaluation.performance --samples 3
uv run python -m parking_assistant.guardrails.evaluation `
  --output evaluation/security_report.json

# Unified report
uv run python -m parking_assistant.evaluation.stage1_report
```

## 9. Final tests and coverage

Commands:

```powershell
uv run ruff check .
uv run mypy
uv run pytest

$env:RUN_INTEGRATION_TESTS = "1"
$env:LANGSMITH_TRACING = "false"
uv run pytest -m integration --no-cov
```

Verified results:

- Ruff: passed
- Strict mypy: passed across 76 source files
- Default suite: 119 passed, 5 opt-in integrations skipped
- Branch coverage: 91.28% (90% gate passed)
- Real integrations: 5 passed against configured OpenAI, PostgreSQL, and Weaviate services
- Final demo trace generation: passed in `parking-assistant-stage1`

The remaining warnings are the previously observed benign LangChain/Pydantic structured-output
serializer warnings, one LangSmith dependency deprecation warning on Python 3.12, and a local
pytest cache permission warning. Validated outputs and all gates pass.

## 10. Known limitations

- PII and exfiltration pattern detection is defense in depth and may have false positives or
  false negatives; it is not authorization or a complete DLP system.
- The public name allowlist must be reviewed when approved public content changes.
- The configurable default plate pattern is intentionally broad demo validation and may match
  unrelated uppercase alphanumeric tokens.
- Answer-quality evidence uses 12 cases and an LLM judge; expand it for production evaluation.
- Three performance samples are presentation-scale. External service latency varies, and the
  PostgreSQL result includes connection warm-up behavior.
- Reservation state is process-local, has no TTL or authenticated ownership, and is lost on
  restart. No reservation is checked for conflicts, persisted, approved, confirmed, or booked.

## 11. Stage 2 next work

Stage 2 may introduce an authenticated administrator interface, explicit reservation lifecycle,
human approval/rejection, LangGraph interrupt/resume, and durable state. Preserve public/private
data separation, sanitized general traces, trace-disabled raw extraction, session ownership,
deterministic validation, and the rule that no LLM can authorize or persist a reservation.
Define retention/deletion and least-privilege access before making reservation state durable.
MCP remains Stage 3 and must not precede approval and authorization boundaries.

Stage 1 is finalized and ready for submission/presentation. Stage 2 may now begin with administrator interaction and human approval.
