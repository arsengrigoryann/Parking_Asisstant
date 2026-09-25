# Stage 3 performance baseline

| Operation | Samples | Failures | Average ms | p50 ms | p95 ms | HTTP boundary |
|---|---:|---:|---:|---:|---:|:---:|
| postgresql_authorization_lookup | 3 | 0 | 3.64 | 3.45 | 4.29 | no |
| file_append | 3 | 0 | 2.49 | 1.91 | 3.54 | no |
| duplicate_idempotent_invocation | 3 | 0 | 5.78 | 6.21 | 6.25 | no |
| mcp_tool_invocation | 3 | 0 | 56.53 | 30.42 | 103.25 | yes |
| full_approved_recording_path | 3 | 0 | 33.38 | 30.46 | 39.79 | yes |

- PostgreSQL lookup uses the configured real database.
- File append isolates serialization, locking, duplicate scan, flush, and fsync using an in-memory synthetic reader.
- MCP measurements use authenticated localhost Streamable HTTP and the LangChain MCP client.
- Full approved recording includes transport, PostgreSQL reload, authorization, and file recording.
- Measurements are presentation-scale observations in this environment, not SLA guarantees.
- All database rows and temporary files are synthetic and removed after the run.
