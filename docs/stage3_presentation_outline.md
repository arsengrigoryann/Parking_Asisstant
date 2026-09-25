# Stage 3 presentation outline (6 slides)

## 1. Goal: record only human-approved reservations

- One sentence: MCP adds a bounded, externally callable recording capability.
- Visual: user request -> human approval -> MCP recorder -> configured text file.
- Emphasize that authorization remains in the Stage 2 lifecycle.

## 2. Architecture and authorization boundary

- Diagram: administrator API commits `APPROVED` in PostgreSQL, then sends only
  `reservation_id` over authenticated Streamable HTTP.
- Show the MCP server reloading status and decision time from PostgreSQL.
- Callout: caller-provided PII, status, timestamps, and paths are impossible by schema.

## 3. Tool and server design

- Screenshot: Inspector connected plus the single-tool list.
- Screenshot crop: schema containing only `reservation_id`.
- Small labels: localhost bind, bearer middleware, stateless Streamable HTTP, typed result.

## 4. Security, retry, and idempotency

- Flow: database approval -> MCP attempt -> temporary failure -> HTTP 503 -> safe retry.
- Diagram: exclusive file lock -> scan canonical lines -> append/flush/fsync or
  `already_recorded`.
- Results callouts: 4/4 unauthorized states blocked; zero sequential or concurrent duplicates.

## 5. Real Inspector and file evidence

- Screenshots: approved invocation result and five-field text record including facility name.
- Paired screenshot: repeat invocation reports `already_recorded`, line count remains one.
- Optional inset: pending invocation rejected with the file unchanged.

## 6. Results and Stage 4 handoff

- Screenshot/table: `evaluation/stage3_report.md` reliability and measured latency summary.
- Clearly label performance as a local presentation-scale baseline, not an SLA.
- Next: Stage 4 may orchestrate the existing user, human-approval, and MCP boundaries without
  weakening any authorization check.
