# Stage 3 final report

## Outcome

Stage 3 is finalized. MCP records an approved reservation; it never authorizes one. The executed flow crossed the MCP boundary with only `reservation_id`, reloaded the authoritative PostgreSQL row, and wrote the configured file exactly once.

## Executed reliability and security evidence

| Check | Result |
|---|---:|
| Approved cases passed | 2 |
| Unauthorized cases blocked | 4/4 |
| Duplicate records created | 0 |
| Concurrent duplicate records created | 0 |
| Authentication rejections | 1 |
| Transport failures | 0 |
| File-format checks passed | 6 |
| Configured path enforced | passed |
| Retry after temporary MCP failure | passed |

Unauthorized cases were `PENDING_APPROVAL`, `REJECTED`, `CANCELLED`, and an unknown identifier. None changed the output file.

## MCP Inspector and transport

- Authenticated Streamable HTTP transport passed: passed.
- Official Inspector CLI passed: passed.
- Exposed business tools: 1 (`record_approved_reservation`).
- Accepted input fields: `reservation_id`.
- Successful Inspector invocation returned a typed idempotent recording outcome.

## Performance baseline

| Operation | Samples | Failures | Average ms | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| postgresql_authorization_lookup | 3 | 0 | 3.64 | 3.45 | 4.29 |
| file_append | 3 | 0 | 2.49 | 1.91 | 3.54 |
| duplicate_idempotent_invocation | 3 | 0 | 5.78 | 6.21 | 6.25 |
| mcp_tool_invocation | 3 | 0 | 56.53 | 30.42 | 103.25 |
| full_approved_recording_path | 3 | 0 | 33.38 | 30.46 | 39.79 |

These presentation-scale measurements are observations in this environment, not SLA guarantees.

## Security assessment

- Only reservation_id crosses MCP; PostgreSQL reloads authoritative state.
- Pending, rejected, cancelled, and unknown reservations produced no record.
- Missing or invalid bearer authentication is rejected before MCP dispatch.
- The output path is configuration-only and cannot be supplied by a tool caller.
- File locking and canonical-line checks prevented sequential and concurrent duplicates.
- An MCP outage after approval preserves the committed decision for a safe retry.

## Quality gates

- Ruff: passed.
- Strict mypy: passed.
- Default suite: 170 passed, 9 skipped.
- Real integrations: 9 passed.
- Coverage: not collected by project policy.

## Known limitations

- Bearer authentication is demo-grade and the localhost endpoint has no TLS.
- The lock coordinates a shared local filesystem, not distributed storage.
- Canonical-line idempotency cannot distinguish two rows with identical display fields.
- Confirmed-record retention and automatic outage reconciliation are not implemented.
- Performance measurements are small environment-specific observations, not an SLA.
