# Stage 3 final handoff

## 1. Final Stage 3 architecture

The official MCP Python SDK 2.2 server exposes stateless Streamable HTTP at `/mcp` and exactly one
tool: `record_approved_reservation(reservation_id: UUID)`. The authenticated Stage 2 administrator
API remains the only approval authority. After committing `APPROVED`, deterministic application
code invokes MCP with only the identifier. The server reloads PostgreSQL, validates status and
`decision_at`, and writes the configured file under an exclusive lock.

## 2. End-to-end results

The executable synthetic flow passed ordinary API approval/recording and approval followed by a
temporary MCP failure and retry. Pending, rejected, cancelled, and unknown identifiers were all
rejected without changing the file. The approved line was written exactly once, and a repeat call
returned `already_recorded`.

## 3. Inspector verification

The pinned official `@modelcontextprotocol/inspector@2.5.0` CLI connected to the real authenticated
localhost Streamable HTTP server. It observed one tool, an input schema containing only
`reservation_id`, and a successful typed invocation. Raw output and the short-lived synthetic
bearer token were not retained. Manual screenshot steps are documented separately.

## 4. Reliability and security results

Executed results: 2 approved cases passed; 4/4 unauthorized cases blocked; 0 sequential duplicate
records; 0 concurrent duplicate records across 16 calls; 1 unauthenticated request rejected; 0
transport failures; 6 file-format checks passed; configured-path enforcement passed; retry after a
temporary MCP failure passed. MCP records but never authorizes reservations.

## 5. Performance results

The three-sample local baseline recorded zero failures for all five operations. Average/p50/p95
milliseconds were: PostgreSQL authorization lookup 3.64/3.45/4.29; isolated file append
2.49/1.91/3.54; duplicate invocation 5.78/6.21/6.25; authenticated MCP tool invocation
56.53/30.42/103.25; full approved recording 33.38/30.46/39.79. These are environment-specific
presentation measurements, not SLA guarantees.

## 6. Report and evidence locations

- `evaluation/stage3_report.md` and `.json`: final combined report.
- `evaluation/stage3_verification_report.json`: executed E2E and Inspector evidence.
- `evaluation/stage3_performance_report.md` and `.json`: reproducible latency baseline.
- `docs/stage3_screenshot_checklist.md`: exact eight-shot sequence and token precautions.
- `docs/stage3_presentation_outline.md`: concise six-slide visual presentation plan.

## 7. Test results

- Ruff: passed.
- Strict mypy: passed across 112 source files.
- Default suite: 170 passed, 9 opt-in integrations skipped.
- Real integrations: 9 passed, including PostgreSQL plus authenticated MCP Streamable HTTP.
- Coverage is intentionally not collected and has no threshold, per user request.

The full integration gate initially exposed that an injected reservation service still caused an
implicit network MCP client when `.env` contained a token. Auto-wiring is now limited to the
app-owned production construction path; injected components explicitly provide the recorder. The
two affected tests passed focused verification, followed by all nine integrations.

## 8. Known limitations

- Bearer authentication is assignment/demo-grade, without accounts, scopes, rotation, or TLS.
- LangChain's native MCP integration is currently beta; dependencies are locked for reproducibility.
- Idempotency follows the required identical canonical-line check. Two distinct reservations with
  exactly identical four display fields are indistinguishable in the required text format.
- The lock design is for a shared local filesystem, not distributed/network storage.
- Confirmed-record retention/deletion and recovery reconciliation are not automated.

## 9. Stage 4 next work

Stage 4 may orchestrate the complete user, human-approval, and MCP recording workflow using
LangGraph, then add complete workflow integration/E2E and load testing. It should reuse the stable
Stage 2 and Stage 3 boundaries rather than moving authorization into MCP or an LLM.

## 10. Precautions Stage 4 must preserve

Keep PostgreSQL authoritative, keep lifecycle mutation behind authenticated human endpoints, pass
only `reservation_id` over MCP, reload and validate every row server-side, never expose the MCP
tool to general LLM choice, preserve post-commit retry semantics, and retain locked idempotent file
writes. Do not place PII in graph state, Weaviate, tracing, logs, or MCP results.

> Stage 3 is finalized. Stage 4 may now orchestrate the complete user, approval, and MCP recording pipeline with LangGraph.
