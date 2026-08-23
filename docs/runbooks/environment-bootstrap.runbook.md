---
runbook:
  title: Environment Bootstrap
  description: Set up a standalone Metaproc source checkout and progress from the offline smoke to optional live adapters and GCP Batch.
  category: metaproc
---
# Environment Bootstrap

Use this runbook to set up a fresh standalone Metaproc checkout.
The first verification path is deterministic and offline.
Adapter and cloud credentials are optional and only needed when you choose those
execution paths.

For framework development, also read [development.md](../development.md).
For installed CLI use, see [installation.md](../installation.md).

## 1. Install the Locked Development Environment

Metaproc requires Python 3.12 or newer, uv, Node 24.18.0, and npm 11.10.0.

```bash
git clone https://github.com/jlevy/metaproc.git
cd metaproc
make install
make lint-check
```

Every `make` target needs uv and the pinned Node on `PATH`. Install them directly, or
run the repository’s bootstrap, which installs exactly the pinned versions after
verifying each download against a pinned checksum:

```bash
bash devtools/ensure-toolchain.sh
```

The script is idempotent: it reports and exits when a satisfying toolchain is already
installed, and otherwise installs into `~/.local`, which must be on `PATH`. Claude Code
and Codex both run it automatically at session start, so agent sessions begin with a
working toolchain. It installs the repository’s pins rather than the newest releases; a
newer Node major would ship an npm outside the `engines` range that `engine-strict`
enforces, so `npm ci` would then refuse to install.
[Agent toolchain bootstrap](../agent-toolchain-bootstrap.md) records the pins it
installs and how they are guarded.

`make install` syncs the exact committed Python and JavaScript lockfiles and installs
the repository’s Lefthook checks.
Do not use an activated virtual environment or invoke `pip` directly.

## 2. Run the Offline End-to-End Smoke

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-local.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-local
```

This runs a nested three-step DAG and verifies its output files and invocation order.
It does not need a model CLI, API key, network call, cloud project, or downstream
package.

## 3. Configure Only the Adapters You Use

Metaproc loads `.env` from the nearest ancestor directory without replacing variables
already present in the shell.

```bash
cp .env.example .env
```

Remove or leave unset every placeholder you do not use; never commit `.env`. Common
adapter credential choices are:

| Adapter | Local credential |
| --- | --- |
| Claude Code | `claude login` or `ANTHROPIC_API_KEY` |
| Codex | `codex login` or `OPENAI_API_KEY` |
| Gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or Vertex ADC |
| pi | provider-specific API key or Vertex ADC |

The exact modes and cloud-secret forms are documented in
[credential setup](credential-setup.runbook.md).

Survey configuration without making model calls:

```bash
uv --config-file uv.toml run --frozen metaproc auth-check
```

Then probe only the profile you intend to use:

```bash
uv --config-file uv.toml run --frozen metaproc auth-check \
  --live \
  --variant <execution-profile>
```

Live probes may consume provider quota.
The per-adapter process specs in [`process/self-test`](../../process/self-test/) provide
narrower diagnostics.

## 4. Configure GCP Only for Cloud Execution

Install the development environment with the `gcp-batch` optional dependencies
(`make install` already does this), authenticate with Application Default Credentials or
`GCP_CREDENTIALS_BASE64`, and set the relevant `METAPROC_GCP_*` values from
[`.env.example`](../../.env.example).

Before any live submission, render a Batch job locally:

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-cloud.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-cloud-plan \
  --var GCP_PROJECT=your-project \
  --var IMAGE=us-central1-docker.pkg.dev/your-project/tools/metaproc:latest
```

The committed cloud self-test uses `--dry-run`; it does not submit a job or create
spend. Live projects, service accounts, images, repositories, networks, Filestore,
secrets, and downstream workflow packages remain operator-owned configuration.
See [cloud dispatch](cloud-dispatch.runbook.md) for live operations.

## 5. Run the Complete Repository Gate

```bash
make verify
```

This checks locks, formatting, lint, types, local documentation links, public-package
hygiene, supply-chain policy, tests, dependency audits, source/wheel contents, and an
isolated installed-wheel smoke.

## Where to Go Next

- [Operator reference](../../src/metaproc/docs/metaproc-operator-reference.md) — run,
  status, pool, trace, and recovery commands.
- [Credential setup](credential-setup.runbook.md) — adapter and Secret Manager details.
- [Adapter compatibility](adapter-compatibility.runbook.md) — provider/model routing.
- [Cloud dispatch](cloud-dispatch.runbook.md) — live Batch monitoring and recovery.

Domain-specific process specs, schemas, fixtures, handlers, and playbooks belong in the
consumer repository and should link back to these framework runbooks.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
