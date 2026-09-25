# Stage 2 final evaluation report

## 1. Stage 2 architecture

Stage 2 adds durable reservation requests, authenticated human decisions, read-only administrator assistance, and a restart-safe LangGraph interrupt/resume workflow. It does not book spaces or implement MCP.

- Explicit escalation submits one validated complete draft to PostgreSQL.
- A durable reservation/workflow/thread mapping addresses one PostgresSaver thread.
- The graph pauses at a real human interrupt and stores identifiers/status only.
- The authenticated administrator API is the only lifecycle decision authority.
- Resume input is non-authoritative; the graph re-reads PostgreSQL before routing.
- The LangChain review component is read-only, subordinate, and trace-disabled.

## 2. Durable reservation lifecycle

Validated drafts move from `PENDING_APPROVAL` to exactly one terminal decision. Opaque idempotency keys make repeated escalation and repeated identical decisions safe.

## 3. Authenticated administrator interaction

All review and decision routes require the configured bearer token. Authoritative reservation fields are loaded directly from PostgreSQL.

## 4. Administrator LangChain review component

The model produces presentation-only structured text with no tools or decision field. PII-bearing model calls run with tracing disabled and deterministic validators reject recommendations and availability guarantees.

## 5. LangGraph human-in-the-loop workflow

The real graph pauses at `wait_for_human`, resumes the same durable thread, and routes only after `verify_decision` reloads PostgreSQL.

## 6. Database-authoritative decision model

Only authenticated lifecycle endpoints can approve, reject, or cancel. Neither the administrator LLM nor LangGraph resume payload can authorize a transition.

## 7. Durability and restart behavior

Restart/resume integration: passed. The tested workflow resumed by its persisted thread after rebuilding database and graph service objects.

## 8. Privacy and checkpoint protections

Checkpoint PII scan: passed. Graph state, interrupt payloads, mappings, and checkpoints contain safe identifiers/status; raw customer fields remain in the reservation database.

## 9. End-to-end validation

- Approval path: passed
- Rejection path: passed
- Unauthenticated access rejected: passed
- Read-only administrator review: passed
- Studio graph discovery: passed

## 10. Performance results

| Operation | Samples | Average ms | p50 ms | p95 ms | Failures |
|---|---:|---:|---:|---:|---:|
| reservation_submission | 3 | 21.88 | 20.40 | 25.76 | 0 |
| workflow_start_until_interrupt | 3 | 5576.75 | 5571.68 | 5598.43 | 0 |
| admin_reservation_lookup | 3 | 12.88 | 13.25 | 13.46 | 0 |
| admin_review_generation | 3 | 1554.35 | 1579.34 | 1806.45 | 0 |
| approve_lifecycle_mutation | 3 | 16.83 | 13.56 | 24.67 | 0 |
| reject_lifecycle_mutation | 3 | 11.41 | 11.50 | 13.01 | 0 |
| graph_resume_after_decision | 3 | 5505.36 | 5484.99 | 5541.70 | 0 |
| complete_approval_workflow | 3 | 11147.48 | 11142.94 | 11193.47 | 0 |

OpenAI-dependent review generation is separated from deterministic operations. These measurements are an observed baseline, not an SLA.

## 11. Tests and coverage

- Default suite: 144 passed; 8 opt-in integrations skipped
- Branch coverage: 90.33%
- Real integrations: 8 passed
- Ruff: passed
- Strict mypy: passed

## 12. Known limitations

- Bearer-token administrator authentication is demo-grade and has no user accounts.
- Generated review text depends on OpenAI and is never authoritative.
- Approval records a decision but does not recheck capacity, allocate, or book a space.
- Reservation and checkpoint retention require a production deletion policy.
- A committed decision can require an idempotent retry if later graph resume fails.
- Measured model/network latency is environment-specific and is not an SLA.
