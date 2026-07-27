---
process:
  name: self-test-local
  description: >-
    Standalone local end-to-end self-test. Runs the deterministic offline
    example as a nested process and verifies all three outputs. Requires no
    adapter binary, credentials, network access, or downstream package.

  steps:
    - id: run-offline-example
      mode: code
      command: >-
        bash -lc "cd ../.. && uv --config-file uv.toml run --frozen metaproc run-process examples/offline-smoke/offline-smoke.process.md --var RUNS_DIR={{run.dir}} --var RUN_ID=offline"
      description: Execute the packaged three-step offline example.

    - id: verify-outputs
      mode: code
      command: >-
        bash -lc "test -f '{{run.dir}}/offline/s1.txt' && test -f '{{run.dir}}/offline/s2.txt' && test -f '{{run.dir}}/offline/s3.txt' && test \"$(cat '{{run.dir}}/offline/invocations.log')\" = $'s1\\ns2\\ns3'"
      description: Verify the output files and dependency-order invocation log.
      needs: [run-offline-example]
---
# Local Self-Test

End-to-end verification of Metaproc’s standalone execution path.
It invokes the
[offline smoke example](../../examples/offline-smoke/offline-smoke.process.md) as a
nested process, then checks its files and dependency order.

## Steps

1. **run-offline-example** — execute the deterministic three-step DAG.
2. **verify-outputs** — confirm each output and the `s1 → s2 → s3` invocation order.

## Usage

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-local.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-local
```

Downstream packages should keep their domain fixtures, schemas, handlers, QA, and
end-to-end process tests in their own repositories.
