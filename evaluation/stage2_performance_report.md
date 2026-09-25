# Stage 2 performance baseline

| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | External model/network |
|---|---:|---:|---:|---:|---:|:---:|
| reservation_submission | 3 | 0 | 21.88 | 20.40 | 25.76 | no |
| workflow_start_until_interrupt | 3 | 0 | 5576.75 | 5571.68 | 5598.43 | no |
| admin_reservation_lookup | 3 | 0 | 12.88 | 13.25 | 13.46 | no |
| admin_review_generation | 3 | 0 | 1554.35 | 1579.34 | 1806.45 | yes |
| approve_lifecycle_mutation | 3 | 0 | 16.83 | 13.56 | 24.67 | no |
| reject_lifecycle_mutation | 3 | 0 | 11.41 | 11.50 | 13.01 | no |
| graph_resume_after_decision | 3 | 0 | 5505.36 | 5484.99 | 5541.70 | no |
| complete_approval_workflow | 3 | 0 | 11147.48 | 11142.94 | 11193.47 | no |

- Deterministic operations use the configured PostgreSQL and PostgresSaver paths.
- Admin review generation calls the configured OpenAI model and is reported separately.
- Network and model latency is an observed presentation baseline, not an SLA.
- Complete workflow excludes review generation so model latency does not obscure orchestration.
- Synthetic identities are deleted after the benchmark.
