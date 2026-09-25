# Stage 3 screenshot checklist

Use synthetic reservation data only. Keep the MCP server, administrator API, and MCP Inspector
visible in separate terminals. Crop or redact every bearer-token field before saving a screenshot.

## Preparation

```powershell
uv sync --locked
uv run alembic upgrade head
uv run python -m parking_assistant.db.seed
uv run python -m parking_assistant.mcp.server
```

In another terminal, run `uv run uvicorn parking_assistant.api.main:app --reload`, then create a
synthetic request with `uv run python -m parking_assistant.api.cli --interactive` and `:submit`.
Keep its returned `reservation_id`.

Launch the official Inspector web UI with Node 22.19 or newer:

```powershell
& "$env:ProgramFiles\nodejs\npx.cmd" --yes `
  @modelcontextprotocol/inspector@2.5.0 --web `
  --transport http --server-url http://127.0.0.1:8765/mcp `
  --header "Authorization: Bearer $env:MCP_SERVER_TOKEN"
```

If the header value is visible, mask it before capturing. Never paste the token into a report.

## Required evidence

1. **Authenticated connection to `/mcp`**
   - Action: open the Inspector URL printed by the launcher and connect with Streamable HTTP.
   - Expected: connected state for `http://127.0.0.1:8765/mcp`; no 401 response.
   - Why: proves the official client reaches the real authenticated transport.

2. **Single business tool**
   - Action: select **Tools** and refresh the tool list.
   - Expected: only `record_approved_reservation` is listed.
   - Why: proves the MCP authority is narrowly scoped.

3. **Identifier-only schema**
   - Action: expand `record_approved_reservation` in Inspector.
   - Expected: the input schema has exactly one field, `reservation_id`; no PII, status, or path.
   - Why: proves callers cannot supply authoritative reservation data or the output destination.

4. **Approved invocation**
   - Action: approve the request through the human-controlled API, then invoke the tool in
     Inspector with its UUID.

     ```powershell
     $adminHeaders = @{ Authorization = "Bearer $env:ADMIN_API_TOKEN" }
     Invoke-RestMethod -Method Post -Headers $adminHeaders `
       "http://127.0.0.1:8000/admin/reservations/<RESERVATION_ID>/approve"
     ```

   - Expected: Inspector returns the same reservation ID and `recorded` or `already_recorded`.
   - Why: demonstrates PostgreSQL-authorized recording over real Streamable HTTP.

5. **Resulting text file**
   - Action: run `Get-Content $env:MCP_RESERVATION_FILE`.
   - Expected: one five-field line: `Name | Car Number | Facility | Reservation Period | Approval Time`.
   - Why: demonstrates the required side effect and canonical format.

6. **Repeated invocation without a duplicate**
   - Action: invoke the same UUID again, then run
     `(Get-Content $env:MCP_RESERVATION_FILE).Count`.
   - Expected: `already_recorded` and an unchanged line count of `1` for the clean demo file.
   - Why: demonstrates sequential idempotency.

7. **Pending attempt rejected**
   - Action: submit a second synthetic request but do not approve it; invoke its UUID in Inspector.
   - Expected: a safe tool error stating that the reservation is not approved; file unchanged.
   - Why: demonstrates that MCP cannot authorize a reservation.

8. **Final Stage 3 report**
   - Action: open `evaluation/stage3_report.md` after running the evidence commands.
   - Expected: all factual reliability/security checks, Inspector status, performance table, and
     quality gates are visible.
   - Why: provides one concise summary tied to machine-readable JSON evidence.

The automated Inspector evidence command is:

```powershell
uv run python -m parking_assistant.evaluation.stage3_verification --inspector
```

It uses short-lived synthetic credentials and retains only aggregate results, never the token or
raw Inspector output.
