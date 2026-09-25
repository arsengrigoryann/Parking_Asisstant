# Stage 2 final handoff

## 1. Final architecture

Explicit escalation submits one complete validated draft to PostgreSQL, creates/reuses one durable
reservation/workflow/thread mapping, and starts the real PostgresSaver-backed LangGraph workflow.
The graph pauses at `wait_for_human`. An authenticated human records the authoritative lifecycle
decision through the administrator API; resume then re-reads PostgreSQL before routing to an
approved, rejected, or cancelled terminal node. The LangChain administrator brief remains
read-only, subordinate, and trace-disabled.

## 2. End-to-end results

Real approval and rejection paths passed. Coverage also verifies refusal to resume before a
database decision, idempotent duplicate decision/resume, unauthenticated access rejection,
restart/resume with rebuilt service objects, read-only administrator assistance, and a raw
checkpoint scan that found no synthetic name or plate.

## 3. Performance results

`evaluation/stage2_performance_report.{json,md}` contains three real samples per operation and zero
failures. Key averages in this environment: submission 21.88 ms, administrator lookup 12.88 ms,
OpenAI review generation 1554.35 ms, approval 16.83 ms, rejection 11.41 ms, graph start-to-interrupt
5576.75 ms, resume 5505.36 ms, and complete approval workflow 11147.48 ms. These are presentation
baselines, not SLAs.

Run again with:

```powershell
uv run python -m parking_assistant.evaluation.stage2_performance --samples 3
uv run python -m parking_assistant.evaluation.stage2_report
```

## 4. Studio setup and status

`langgraph.json` exposes the actual `approval_workflow`; `uv run langgraph dev --no-browser`
successfully registered it. EU Studio command:

```powershell
uv run langgraph dev --studio-url https://eu.smith.langchain.com
```

The development runtime uses its tooling checkpointer only; production/demo code still uses the
explicit PostgresSaver provider. The currently locked transitive `langgraph-api 0.5.42` reports an
EOL warning in the dev CLI and should be upgraded in a dedicated compatibility change.

## 5. Evidence locations

- Final report: `evaluation/stage2_report.{json,md}`
- Performance: `evaluation/stage2_performance_report.{json,md}`
- Executed gate totals: `evaluation/stage2_verification_report.json`
- Screenshot plan: `docs/stage2_screenshot_checklist.md`
- Seven-slide content: `docs/stage2_presentation_outline.md`

## 6. Final verification

- Ruff: passed
- Strict mypy: passed across 99 source files
- Default suite: 144 passed, 8 opt-in integrations skipped
- Branch coverage: 90.33% (90% gate passed)
- Real integrations: 8 passed against configured OpenAI, PostgreSQL, Weaviate, FastAPI, and
  PostgresSaver paths
- Studio discovery: passed; `approval_workflow` registered

## 7. Known limitations

- Bearer-token administrator authentication is demo-grade and has no accounts/scopes.
- Generated review text is OpenAI-dependent and never authoritative.
- Approval does not recheck capacity, allocate, notify, record a confirmed booking, or call MCP.
- Reservation/checkpoint retention and deletion are not automated.
- A committed decision may require an idempotent resume retry after a later checkpointer failure.
- Model/network latency varies by environment and is not an SLA.

## 8. Stage 3 next task

Implement a least-privilege MCP server that records a confirmed reservation only after the existing
administrator-approved PostgreSQL state has been independently validated. Add authorization,
strict schemas, idempotency, and real boundary tests without moving lifecycle authority into the
LLM or MCP client.

## 9. Stage 3 precautions

- Preserve PostgreSQL as lifecycle authority and PostgresSaver as workflow durability.
- Never treat LLM prose, graph resume data, or an MCP request as proof of approval.
- Keep customer PII out of Weaviate, graph state, interrupts, checkpoints, and normal traces.
- Keep the administrator review agent read-only and its PII-bearing invocation trace-disabled.
- Reuse reservation/workflow IDs and require an `APPROVED` database row before any recording side
  effect.
- Make confirmed-reservation recording idempotent and least-privilege; do not add notifications or
  Stage 4 orchestration.

> Stage 2 is finalized. Stage 3 may now implement the MCP server that records only administrator-approved reservations.
