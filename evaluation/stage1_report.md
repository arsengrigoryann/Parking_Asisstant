# Stage 1 final evaluation report

## 1. System scope

Stage 1 user assistant: public static RAG, deterministic PostgreSQL reads, intent routing, ephemeral reservation detail collection, and privacy/security guardrails. No approval, persistence, LangGraph, or MCP.

## 2. Retrieval evaluation

Dataset: 18 queries; K=5.

| Mode | Precision@K | Recall@K | MRR |
|---|---:|---:|---:|
| semantic | 0.244 | 1.000 | 0.755 |
| bm25 | 0.222 | 0.944 | 0.713 |
| hybrid | 0.244 | 1.000 | 0.807 |

The application uses hybrid retrieval: it combines semantic matching with exact term signals while retaining the mandatory public-data filter.

## 3. Answer-quality evaluation

Cases: 12/12; failures: 0.

| Correctness | Groundedness | Relevance |
|---:|---:|---:|
| 1.000 | 1.000 | 1.000 |

LangSmith experiment: `parking-assistant-stage1-answer-quality-e305de81`.

## 4. Performance baseline

| Operation | Samples | Avg ms | p50 ms | p95 ms | Failures |
|---|---:|---:|---:|---:|---:|
| hybrid_retrieval | 3 | 411.05 | 207.39 | 773.92 | 0 |
| static_rag_request | 3 | 1634.65 | 1552.95 | 1868.79 | 0 |
| dynamic_postgresql_request | 3 | 1694.83 | 4.55 | 4569.31 | 0 |
| intent_classification | 3 | 1285.34 | 906.91 | 1986.00 | 0 |
| complete_assistant_request | 3 | 873.72 | 865.38 | 1005.33 | 0 |

Network-dependent measurements are a reproducible baseline, not deterministic service-level guarantees.

## 5. Security evaluation

- Cases passed: 20/20
- PII leakage rate: 0.000
- Malicious-request block rate: 1.000
- Benign-request pass rate: 1.000
- Cross-session leakage count: 0

## 6. Test and coverage status

- Unit/default suite: 119 passed; 5 opt-in tests skipped
- Coverage: 91.28%
- Real integrations: 5 passed
- Ruff: passed
- Strict mypy: passed

## 7. Known limitations

- OpenAI and Weaviate latency is network-dependent and varies by environment.
- Answer-quality scores are model-judged against a small curated public dataset.
- PII and injection detection are defense in depth and cannot cover every phrasing.
- Reservation state is process-local, unauthenticated, non-durable, and not booked.
- Stage 1 has no interval conflict check, human approval, persistence, LangGraph, or MCP.
