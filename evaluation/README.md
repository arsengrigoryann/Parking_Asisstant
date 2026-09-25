# Evaluation

All datasets contain only approved public information or explicitly synthetic security values.
Do not add real customer or reservation data.

## Retrieval evaluation

`retrieval_dataset.jsonl` contains manually curated static-knowledge judgments. Run the
lightweight comparison after ingestion with:

```powershell
uv run python -m parking_assistant.rag.evaluation --mode semantic --k 5
uv run python -m parking_assistant.rag.evaluation --mode bm25 --k 5
uv run python -m parking_assistant.rag.evaluation --mode hybrid --k 5
uv run python -m parking_assistant.rag.evaluation --all --k 5 `
  --output evaluation/retrieval_report.json
```

The utility reports macro-averaged document-level Precision@K, Recall@K, and MRR. It does
not evaluate generated answers; answer generation is outside Stage 1B. Do not place customer
PII here. The application uses hybrid mode; the three-mode command provides the final comparison.

## Answer quality

`answer_quality_dataset.jsonl` contains 12 stable public questions and reference facts.

```powershell
uv run python -m parking_assistant.evaluation.answer_quality --upload-langsmith
```

This generates JSON and Markdown reports and, when configured, publishes the measured answers
and correctness/groundedness/relevance feedback as a LangSmith offline experiment.

## Performance

```powershell
uv run python -m parking_assistant.evaluation.performance --samples 3
```

The sample count controls OpenAI cost. JSON and Markdown output include average, p50, p95, and
failures. Network-dependent results are a baseline, not deterministic guarantees.

## Security evaluation

`security_dataset.jsonl` contains 20 deterministic cases using synthetic identities only. Run:

```powershell
uv run python -m parking_assistant.guardrails.evaluation `
  --output evaluation/security_report.json
```

The generated report contains the observed PII leakage rate, malicious-request block rate,
benign-request pass rate, cross-session leakage count, and total pass/fail counts. Re-run the
command after guardrail changes; do not hand-edit or invent report results.

## Unified report

After all reports and `verification_report.json` exist, run:

```powershell
uv run python -m parking_assistant.evaluation.stage1_report
```

This validates and combines the executed artifacts into `stage1_report.json` and
`stage1_report.md`.

## Stage 2 performance and final report

After migrations and seed data are ready, run the presentation-scale administrator/workflow
baseline once:

```powershell
uv run python -m parking_assistant.evaluation.stage2_performance --samples 3
```

It writes `stage2_performance_report.json` and `.md`. Deterministic PostgreSQL/PostgresSaver
operations are separated from OpenAI-dependent administrator review generation. All reservation
identities are synthetic and removed after the run; model/network measurements are not an SLA.

Record the final executed gate totals in `stage2_verification_report.json`, then generate the
validated combined report:

```powershell
uv run python -m parking_assistant.evaluation.stage2_report
```
