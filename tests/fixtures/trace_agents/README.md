---
title: Multi-adapter synthetic trace fixtures
description: Deterministic adapter events, invocation sidecars, and cross-adapter baseline counts for trace extractor tests.
---
# Multi-adapter synthetic trace fixtures

These fixtures provide credential-free, synthetic examples of the event shapes emitted
by the Claude, Codex, Gemini, and Pi adapters.
They retain parser-relevant structure, usage counters, relative timings, and tool
relationships without retaining captured prompts, outputs, paths, identities,
repositories, runs, companies, or customer data.

## Files

- `{claude,codex,gemini,pi}-sample.jsonl` contains representative adapter events.
- `{adapter}-sample.jsonl.invocation.json` contains matching synthetic invocation
  metadata.
- `baseline-synthetic-five-adapter.json` contains non-zero per-adapter tool counts used
  by the trace-rollup regression test.
- `manifest.json` maps each generated fixture to its adapter type.

## Regenerating fixtures

The checked-in generator is deterministic and idempotent:

```bash
uv run python devtools/synthesize_fixtures.py
uv run python devtools/synthesize_fixtures.py --check
```

New captures must never be committed directly.
Run the generator over the private working copy, review the resulting complete-file
diff, run the public hygiene gate, and commit only the synthetic output.

<!-- This document follows std-doc-guidelines.md.
-->
