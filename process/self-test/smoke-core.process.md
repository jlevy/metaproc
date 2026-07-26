---
process:
  name: smoke-core
  description: >-
    Provider-agnostic standalone repository smoke. Runs the committed
    lint, type, documentation, supply-chain, and unit-test gates without
    credentials, model calls, or a downstream workflow package.

  steps:
    - id: lint
      mode: code
      command: >-
        bash -lc "cd ../.. && make lint-check"
      description: Run every non-mutating repository lint and static-analysis gate.

    - id: test
      mode: code
      command: >-
        bash -lc "cd ../.. && make test"
      description: Run the standalone Metaproc unit and offline integration tests.
---
# smoke-core — provider-agnostic health check

Runs the same lint and test commands used by the standalone repository.
No downstream workflow package, adapter credential, cloud project, or model call is
required. The two steps are independent and may run in parallel.

## Steps

1. **lint** — lock drift, Ruff, BasedPyright, Biome, TypeScript, Flowmark, local links,
   public-package hygiene, and supply-chain policy.
2. **test** — the complete standalone pytest suite.

Adapter-specific health (binary, credentials, live dispatch) is covered by
`self-test/smoke-adapter-<name>.process.md` per provider, not here — keeping core free
of adapter-specific env dependencies means a red signal in core always points to a code
regression, never a missing credential.

## Usage

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/smoke-core.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=smoke-core
```

Run a single step during iteration:

```bash
uv --config-file uv.toml run --frozen metaproc run-step \
  process/self-test/smoke-core.process.md \
  --step lint \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=smoke-core
```

## When this is red

Run `make lint-check` or `make test` directly to see the full diagnostic.

## Not covered by smoke-core

- Adapter binaries, credentials, or live dispatch — see
  `self-test/smoke-adapter-<name>.process.md` per provider.
- GCP Batch dispatch — see `process/self-test/test-cloud.process.md`.
- Downstream workflow pipelines — those remain owned and tested by each consumer.
