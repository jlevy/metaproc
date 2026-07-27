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
