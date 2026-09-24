# Stage 1 evaluation

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
