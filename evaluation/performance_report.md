# Stage 1 performance baseline

| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | Network-dependent |
|---|---:|---:|---:|---:|---:|:---:|
| hybrid_retrieval | 3 | 0 | 411.05 | 207.39 | 773.92 | yes |
| static_rag_request | 3 | 0 | 1634.65 | 1552.95 | 1868.79 | yes |
| dynamic_postgresql_request | 3 | 0 | 1694.83 | 4.55 | 4569.31 | no |
| intent_classification | 3 | 0 | 1285.34 | 906.91 | 1986.00 | yes |
| complete_assistant_request | 3 | 0 | 873.72 | 865.38 | 1005.33 | yes |

- OpenAI and Weaviate network latency is environment-dependent, not deterministic.
- PostgreSQL measurements use the configured local Stage 1 database.
- Tracing is disabled during the benchmark to avoid observability overhead.
