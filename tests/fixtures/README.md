# Test fixtures

Captured fixtures in `log_compaction/` and `trace_agents/` are normalized by
`devtools/synthesize_fixtures.py`. The generator keeps only parser-relevant
structure and must be run before committing refreshed captures.

The remaining fixture directories are small, handwritten test projects or
focused signal samples:

- `auth_env/`
- `claude_api_signals/`
- `fingerprint_smoke/`
- `layout_smoke/`
- `pro_cap_exhaustion/`

These are reviewed as source and are intentionally outside the capture
normalizer.

`operations_summary_v1/` holds one real run's `operations-summary.md` and
`resource-usage-summary.md`, written before `sample_source` and
`RetryStepRow.process` joined `metaproc.operations:AgentOperationsSummary/v1`.
Some identifiers are replaced: the run ID, the code revision, the item keys, the
item-level process name, and the two step ids that carried a domain noun. The
remaining step ids and the process-family name are the captured run's own; they
name no deployment on their own. Every field and every figure is as the writer
produced it, which is what makes it evidence that the contract id still reads its
own older documents. Edit it only to record another writer's shape.
