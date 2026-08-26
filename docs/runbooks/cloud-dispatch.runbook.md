---
runbook:
  title: Cloud Dispatch
  description: Prepare, submit, monitor, and recover Metaproc workloads on GCP Batch without embedding consumer-specific policy.
  category: metaproc
---
# Cloud Dispatch

This runbook covers the framework-owned GCP Batch path.
A downstream repository owns its process specs, schemas, handlers, fixtures, images,
infrastructure, and cost approval.
Metaproc owns job construction, worker/orchestrator dispatch, status, logs, scaling,
cancellation, and artifact transport.

Adapter/model routing details live in
[adapter compatibility](adapter-compatibility.runbook.md).
Credential and Secret Manager setup lives in
[credential setup](credential-setup.runbook.md).

## 1. Pre-Launch Gates

Run these gates from the exact branch and source state you intend to ship:

```bash
make verify

uv --config-file uv.toml run --frozen metaproc run-process \
  path/to/workflow.process.md \
  --var RUNS_DIR=/absolute/path/to/runs \
  --var RUN_ID=preflight \
  --dry-run

uv --config-file uv.toml run --frozen metaproc auth-check \
  --live \
  --variant <execution-profile>
```

Also run the consumer repository’s own verification command.
A framework-green result does not validate domain schemas, prompts, handlers, or QA.

## 2. Required Configuration

Start from [`.env.example`](../../.env.example) and set only what the chosen execution
mode requires. Never commit `.env`.

| Variable | Purpose |
| --- | --- |
| `METAPROC_GCP_PROJECT` | GCP project containing Batch resources |
| `METAPROC_GCP_REGION` | Batch region |
| `METAPROC_GCP_SERVICE_ACCOUNT` | Explicit Batch identity; required when a run binds Secret Manager secrets or uses a Secret Manager auth pool |
| `METAPROC_GCP_CONTAINER_IMAGE` | Image that can run Metaproc and the consumer |
| `METAPROC_GCS_BUCKET` | Wheel and workspace artifact transport |
| `METAPROC_GCP_SECRET_GH_TOKEN` | Secret Manager ref used when a private repo must be cloned |
| `METAPROC_GCP_FILESTORE_*` | Optional shared live execution and restart storage |
| `METAPROC_REPO_URL` / `METAPROC_RUN_BRANCH` | Optional repository source for remote bootstrap |
| `METAPROC_WHEEL_GCS` | Optional exact prebuilt Metaproc wheel |
| `METAPROC_WHEEL_SHA256` | Required digest when `METAPROC_WHEEL_GCS` is set |
| `METAPROC_WORKSPACE_GCS` | Optional exact consumer workspace archive |
| `METAPROC_WORKSPACE_SHA256` | Required digest when `METAPROC_WORKSPACE_GCS` is set |

Prefer immutable wheels/images and a pinned workspace artifact for repeatable runs.
Every downloaded wheel or workspace archive must have its corresponding SHA-256 value;
bootstrap fails closed when a URI lacks its digest or the bytes do not match.
Branch checkout is useful during development but is mutable and must point at a pushed
commit containing every required file.

## 3. Render Before Submitting

The committed cloud-plan self-test renders a single-task Batch job and exits without a
network submission or spend:

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-cloud.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-cloud-plan \
  --var GCP_PROJECT=your-project \
  --var IMAGE=us-central1-docker.pkg.dev/your-project/tools/metaproc:latest
```

Inspect the rendered job for the intended project, region, image, service account,
machine type, secret references, and mounts before removing any `--dry-run` gate.

## 4. Dispatch a Multi-VM Process

Use direct cloud dispatch when the process supports multi-VM `gcp-worker` fan-out.
`run-process` remains the orchestration API: the process graph, leases, claims, retries,
resume state, and worker fan-out remain framework-owned.

```bash
uv run metaproc run-process path/to/workflow.process.md \
  --var RUNS_DIR=/mnt/filestore/runs \
  --var RUN_ID=<new-run-id> \
  --backend gcp-worker \
  --cloud \
  --num-workers <count> \
  --max-concurrency <per-worker-count>
```

Add process variables and an execution-profile override only when required by the
consumer. Use `--spot` only when the workflow tolerates preemption.
The product of workers and per-worker concurrency is the maximum task concurrency,
subject to runtime resource and provider limits.

The current flags are implementation-facing and will be replaced together by explicit
`--orchestrator` and `--worker` placement flags.
Do not use those future names in executable commands yet.
Local-orchestrator/cloud-worker execution is also planned, but is rejected until it has
an explicit bidirectional state transport; a laptop path, SSHFS mount, or path alias is
not that transport.

Filestore is scratch state for live execution and restart, not Metaproc’s terminal
durability contract.
Before reclaiming it, the downstream consumer must publish the accepted terminal run
tree to its registered durable object-store contract and verify that publication under
its own policy.

## 5. Dispatch One Task or a Single-Host Process

`metaproc gcp run` is the lower-level single-task Batch primitive.
Use it for a probe, diagnostic, publication task, or one complete local-backend process
that must stay on one host.
Mapped composites require this single-host placement because the `gcp-worker` backend
does not yet support them.
Do not chain multiple `gcp run` calls to recreate process scheduling; the one nested
`run-process` command owns the DAG.

The command sends one command to one Batch task.
By default it builds and ships the current Metaproc wheel and repository workspace.

```bash
# Render only.
metaproc gcp run --dry-run -- python -m metaproc --help

# Submit and stream logs.
metaproc gcp run -- python -m my_consumer.batch_task --shard shard-a

# Install a shipped workspace package before running nested uv commands.
metaproc gcp run \
  --workspace-package packages/my-consumer \
  -- uv run --frozen --project packages/my-consumer my-consumer-task

# Place one complete DAG on one Batch VM with the local backend.
metaproc gcp run \
  --machine-type <machine-type> \
  --workspace-package packages/my-consumer \
  -- metaproc run-process workflows/example.process.md \
    --backend local \
    --var RUN_ID=<new-run-id>

# Submit without waiting.
metaproc gcp run --detach -- python -m my_consumer.batch_task --shard shard-b
```

Useful controls:

- `--no-wheel` uses the image-baked Metaproc.
- `--no-workspace` skips repository transport, so consumer source must already be in the
  image. Nested `uv run` commands use the baked `/opt/venv` without syncing an absent
  project; a shipped Metaproc wheel can still replace the image-baked version.
- `--sync PATH` and `--sync-only PATH` narrow workspace transport.
- `--workspace-package PATH` installs a shipped Python package editable into the baked
  environment. Repeat it for multiple packages; it cannot be combined with
  `--no-workspace`. Installation uses `--no-deps`, so every third-party dependency must
  already be present in the agent image.
  Rebuild the image when that dependency closure changes.
- `--env K=V` adds non-secret configuration.
- `--secret K=REF` binds a Secret Manager version.
  Secret-bearing runs require `METAPROC_GCP_SERVICE_ACCOUNT`; dispatch refuses them
  before artifact upload when the identity is unset.
  The Batch spec carries only `REF`; the container fetches its value under that service
  account before bootstrap.
  Do not replace this with Batch `secret_variables`, because Batch agent logs can expose
  their expanded values.
- A cloud auth pool backed by Secret Manager also requires
  `METAPROC_GCP_SERVICE_ACCOUNT`. Each scalar or fan-out launch acquires its credential
  through the shared pool under that identity; the dispatcher does not inject a
  preferred credential into the container environment.
- `--timeout <seconds>` sets the task deadline.

Container-side hydration requires the agent image to install `metaproc[gcp-batch]` so
the Secret Manager client is available before bootstrap.
When releasing a hydration-contract change, build and canary the new agent image before
deploying the dispatcher that emits the new contract.
A new image can still accept an older dispatcher payload; an old image cannot interpret
the new reference-only payload.
If `GH_TOKEN` is empty and every Claude task reports that it is not logged in after a
dispatcher rollout, verify that the selected image includes container-side hydration.

Do not pass credentials through `--env`. Use Secret Manager references.
For a new identity or secret, first run a harmless canary that tests only for the target
environment variable’s presence.
Inspect both task and agent logs and confirm the canary value does not appear before
dispatching a real provider credential.

## 6. Monitor Through Metaproc

Use the framework commands rather than hand-parsing run directories or calling raw
cloud-provider listing/logging commands:

| Question | Command |
| --- | --- |
| Which runs are active? | `metaproc gcp runs` |
| What is the Batch state? | `metaproc gcp status <run-id>` |
| What are workers logging? | `metaproc gcp logs <run-id> --follow` |
| Stop the run | `metaproc gcp cancel <run-id>` |

For unattended runs, use the supported automation/monitoring mechanism in the active
agent environment and have it call these commands.
During execution, store wrapper logs and restart evidence in the live run tree, never
`/tmp`; publish accepted terminal evidence through the downstream durable object-store
contract before reclaiming scratch.
Filesystem-oriented commands such as `metaproc status`, `pulse`, and `pool status`
require an explicit, locally visible run-directory path.
They do not resolve a cloud run ID through a persistent VM or remote mount.
Use `metaproc gcp status` and `gcp logs` for Batch-native monitoring.

## 7. Recovery

1. Read `metaproc gcp status` and `metaproc gcp logs`. If the run tree is already
   available in the current environment, inspect it with `metaproc status <path>`.
2. Classify the failure: bootstrap, auth, quota, process validation, provider, resource,
   or infrastructure.
3. Fix the owning layer and push/build any code or image change.
4. Re-run with the same `RUN_ID` when completion fingerprints should preserve valid
   work. Use a new run ID only for an intentional clean duplicate.
5. Use `override` or `--force` only after reviewing their audit and caching semantics in
   the [operator reference](../../src/metaproc/docs/metaproc-operator-reference.md).

Plaintext `GH_TOKEN`, `CLAUDE_CODE_CREDS_JSON`, and similar credentials are refused on
cloud dispatch when their registered Secret Manager references are absent.
Keep that fail-closed behavior intact.

## Consumer Boundary

The downstream repository must document:

- how its container image is built and pinned;
- which process variables and execution profiles are approved;
- expected cost and who authorizes it;
- domain preflight and QA;
- the registered durable object-store contract for accepted terminal run trees, plus
  retention and incident-evidence policy;
- the exact Metaproc version or submodule commit used.

Keep those policies out of this framework runbook.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
