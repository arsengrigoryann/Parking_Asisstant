# Integration tests

These tests use the real services configured in `.env`. Run them explicitly after starting Docker services:

```powershell
$env:RUN_INTEGRATION_TESTS = "1"
uv run pytest -m integration --no-cov
```

They apply the current migration and deterministic seed to the configured development database and verify authenticated Weaviate readiness.
