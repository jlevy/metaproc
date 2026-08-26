---
runbook:
  title: Credential Setup
  description: How to configure credentials for each metaproc adapter — Claude OAuth pool, Codex ChatGPT-plan, Gemini modes, pi-cli providers, and GCP Secret Manager.
  category: metaproc
---
# Credential Setup

How to configure credentials for each metaproc adapter.

Bootstrap context (tool installs, gcloud, first-time `auth-check`):
[`environment-bootstrap.runbook.md`](environment-bootstrap.runbook.md).
Routine command surface (`run-process`, `auth status`, `status`, `pool`, `trace`):
[`metaproc-operator-reference.md`](../../src/metaproc/docs/metaproc-operator-reference.md).

## Quick verification

```bash
# Check all adapters and credentials
metaproc auth-check

# Also test live API connectivity
metaproc auth-check --live
```

## Adapters

### Claude Code CLI (`claude-code-cli`)

**Vehicle A — `CLAUDE_CODE_OAUTH_TOKEN` — is the path metaproc deploys.** Long-lived
static bearer tokens minted by `claude setup-token` flow through the labeled credential
pool. The default for `metaproc auth push --adapter claude-code-cli` is
`--vehicle oauth-token`; no flag is needed for the common case.

Vehicle B (stored `/login` credentials) is preserved as a **fallback escape hatch** for
accounts that fail Vehicle A on the pinned CLI version.
Operators opt into it explicitly with `--vehicle login-credentials`. `ANTHROPIC_API_KEY`
is the pay-per-token sidecar for non-subscription dispatch.

#### Vehicle A — `CLAUDE_CODE_OAUTH_TOKEN` (the path)

Static long-lived OAuth token (~1 year per Anthropic).
The token is the credential — slot bootstrap injects it via env var; no
`.credentials.json` is materialized into the slot.
Eliminates the snapshot-staleness failure mode that affected Vehicle B fan-out; see
[the authentication architecture](../arch/arch-authentication.md) for the design.

**Per-account, once per token rotation (~yearly):**

The fast path is `metaproc auth setup`. On a machine signed into the target Anthropic
account, run:

```bash
metaproc auth setup alt1
```

metaproc spawns `claude setup-token` interactively (operator follows the OAuth flow in
their terminal/browser), captures the minted token, pushes it to **both** backends
(local
+ GCP Secret Manager when `METAPROC_GCP_PROJECT` is set), and runs a real-API probe.
  `--backend both` graceful-degrades to local-only when no GCP project is configured.
  The label is operator-chosen (`[a-z0-9-]{1,40}`) — pick names that mean something
  across accounts (`laptop`, `alt1`, `alt2`).

Other input modes against the same command:

```bash
claude setup-token | metaproc auth setup alt1     # piped
metaproc auth setup alt1 --token-file <path>      # file (rare)
```

Single-backend variants:

```bash
metaproc auth setup alt1 --backend local
metaproc auth setup alt1 --backend gcp-secret-manager
metaproc auth setup alt1 --no-probe               # skip live API probe
```

Verify what the pool sees:

```bash
metaproc auth status --backend local           # text dashboard
metaproc auth status --backend local --json    # machine-readable
```

Vehicle A labels are tagged `[A]` in the text view.

**Token-handling discipline:** the token is held in-memory only and never written to
disk by `auth setup`. Inner `auth push` calls bridge it via the
`CLAUDE_CODE_OAUTH_TOKEN` env var scoped to the duration of each push; any pre-existing
operator value is restored on exit.
`--token-file` (when used) is read once and not retained.

**Low-level fallback.** `auth push --vehicle oauth-token` is the underlying operation
and remains available for unusual flows (scripted onboarding without an interactive
terminal, recovering a backend after partial failure, push-only without probe):

```bash
metaproc auth push --adapter claude-code-cli --label alt1 \
  --vehicle oauth-token --token-file /tmp/token-alt1.txt --probe \
  --backend gcp-secret-manager
```

**Per dispatch (no per-dispatch ritual — pool labels stay valid for ~1y):**

```bash
metaproc run-process <spec> \
  --auth-account claude-code-cli \
  --auth-include-labels alt1 --auth-include-labels alt2 \
  --auth-fallback-policy same-provider
```

Cloud dispatch defaults this pool to the GCP Secret Manager backend and therefore
requires `METAPROC_GCP_SERVICE_ACCOUNT`. Every agent launch acquires its own isolated
pool slot; no label is exported as an ambient credential for the run.

Cloud dispatch is identical with `--cloud --backend gcp-worker`; the orchestrator and
worker entrypoints both consume the pool transparently.

**Optional but recommended on push** — annotate quota-group identity so the classifier’s
429 walk routes to a different account on rate-limit:

```bash
metaproc auth push --adapter claude-code-cli --label alt1 \
  --vehicle oauth-token --token-file /tmp/token-alt1.txt --probe \
  --account-id <16-hex-hash-of-account-identity> \
  --organization-uuid <organizationUuid-from-account>
```

When set, a 429 on alt1 will skip every label sharing alt1’s `organization_uuid` (or
`account_id`) and route to a different account.

#### Vehicle B — stored `/login` credentials (escape hatch)

Refresh-rotating OAuth session snapshot from `claude /login`. **Preserved as a
fallback** for the rare case where Vehicle A fails on a specific account (e.g. F10
sub-day-expiry bug or `#37512` Keychain-deletion-on-exit on the pinned CLI version).
Not the path for new deployments — fragile under multi-process fan-out because the
snapshot’s refresh token can be invalidated by external rotation events the pool can’t
observe (Keychain rotation, parallel `claude` processes, MFA re-prompts, server-side
revocation). See research §F12 + Vehicle B safe-mode for details.

```bash
# Local backend
metaproc auth push --adapter claude-code-cli --label laptop \
  --vehicle login-credentials --probe

# GCP Secret Manager backend
metaproc auth push --adapter claude-code-cli --label laptop \
  --vehicle login-credentials --backend gcp-secret-manager --probe
```

`auth push --vehicle login-credentials` reads from macOS Keychain (where `claude /login`
stored the credential).

`metaproc claude-auth` is the single-secret pre-pool path (pushes to
`claude-code-creds-<user>` via `METAPROC_GCP_SECRET_CLAUDE_CREDS`); use
`metaproc auth push` for new deployments.

#### Vehicle C — `ANTHROPIC_API_KEY` (pay-per-token)

For non-subscription dispatch where per-token billing is acceptable:

| Variable | Required | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Get from https://console.anthropic.com/settings/keys |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**`ANTHROPIC_API_KEY` MUST be unset on subscription-backed workers.** Claude Code’s
precedence chain silently prefers `ANTHROPIC_API_KEY` over both Vehicle A and Vehicle B
if all are present, so a stray inherited value bypasses the pool and bills per-token.

#### Quick recap — which vehicle for which case

| Scenario | Use |
| --- | --- |
| Default / multi-account / cloud / single-account | **Vehicle A** (no flag needed — it’s the default) |
| An account that fails Vehicle A push (rare) | Vehicle B via `--vehicle login-credentials` |
| Backwards compat with pre-pool deploys | Vehicle B via legacy `claude-auth` push |
| Pay-per-token (subscription not in use) | `ANTHROPIC_API_KEY` |

Architecture: [arch-authentication.md](../arch/arch-authentication.md).
Operator runbook for full dispatch:
[`metaproc/docs/runbooks/cloud-dispatch.runbook.md`](cloud-dispatch.runbook.md) → *GCP
Batch (Personal Plan)*.

### Pi CLI (`pi-cli`)

Pi supports multiple providers.
Each has its own auth.

**Anthropic provider** (default):

| Variable | Required | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Same key as Claude Code CLI |

**Vertex AI MaaS provider** (`vertex-maas`):

Requires the `metaproc[gcp]` extra (included automatically in the dev dependency group
via `uv sync`). Uses `google.auth.default()` with auto-refreshing credentials — tokens
never expire mid-run.

The `vertex-maas` provider and its MaaS models are **not** built into pi-cli upstream —
they live in a pi-cli-level `models.json`. Metaproc dispatch reads
`~/.pi/agent/models.json` if present (operator-authored), otherwise falls back to the
packaged canonical copy at `src/metaproc/data/pi-models.default.json`. Cloud workers
inherit whichever source the dispatching host resolved, with apiKey and project paths
rewritten for the container at launch time.
Keep your own `~/.pi/agent/models.json` if you have newer/additional models than the
packaged default — the packaged file is strictly a fallback for fresh environments (CI,
new workstations).
See `src/metaproc/cloud/gcp/batch_backend.py` (`build_pi_models_json`)
for the resolution logic.

Credential resolution (in priority order):

| Method | Variable | Notes |
| --- | --- | --- |
| SA key file path | `GOOGLE_APPLICATION_CREDENTIALS` | Standard GCP env var. Points to a JSON key file. |
| Base64-encoded SA key | `GCP_CREDENTIALS_BASE64` | Portable alternative for CI and `.env` files. Auto-decoded to a temp file. |
| Cloud environment | *(none)* | Auto-detected on GCE, GKE, Cloud Run. |
| User credentials | *(none)* | Via `gcloud auth application-default login`. |

```bash
# Option A: point to a key file
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json

# Option B: base64-encode the key (good for .env files and CI secrets)
#   macOS: base64 -i sa-key.json
#   Linux: base64 -w0 sa-key.json
GCP_CREDENTIALS_BASE64=<base64-encoded-key>

# Verify
metaproc auth-check
```

### Codex CLI (`codex-cli`)

Two authentication modes, depending on where you are running:

**Laptop / dev shell / CI — API key (pay-per-token, Vehicle A):**

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes | Get from https://platform.openai.com/api-keys. Used by both pi-cli (openai provider) and the codex-cli adapter. This is API-platform billing, not ChatGPT Pro Codex allowance. |

```bash
export OPENAI_API_KEY=sk-...
```

**Laptop — ChatGPT-plan subscription (OAuth, Vehicle B):**

Use this path when you want Codex usage to attach to the signed-in ChatGPT account and
its Codex allowance.

```bash
codex login                           # opens browser, writes ~/.codex/auth.json
# Required once to enable headless push to Secret Manager:
#   ~/.codex/config.toml should contain  cli_auth_credentials_store = "file"
```

**GCP Batch workers — ChatGPT-plan subscription (via Secret Manager, Vehicle B):**

| Variable | Required | Notes |
| --- | --- | --- |
| `METAPROC_GCP_SECRET_CODEX_CREDS` | Yes | Secret Manager resource name carrying the `~/.codex/auth.json` blob. Workers materialize the file at 0600 under a 0700 parent. |
| `OPENAI_API_KEY` | **MUST NOT be set** | When both are present codex prefers the API key, which silently bypasses the subscription (pay-per-token instead of free-per-request). Leave it unset on workers. |

```bash
metaproc codex-auth push              # push ~/.codex/auth.json to Secret Manager
metaproc codex-auth show              # inspect metadata + IAM (never the payload)
metaproc codex-auth rotate            # push new version, destroy prior enabled versions
```

Then point workers at the secret:

```bash
export METAPROC_GCP_SECRET_CODEX_CREDS=projects/exampletool/secrets/codex-creds-$USER/versions/latest
```

The adapter’s `bootstrap(home)` hook reads `CODEX_CREDS_JSON`, validates that
`tokens.auth_mode == "chatgpt"` (rejecting `apikey` blobs — those should arrive as
`OPENAI_API_KEY` directly), writes `{home}/.codex/auth.json` (mode 0600, parent 0700),
and pops the env var so it does not leak to child processes.
See [arch-authentication.md](../arch/arch-authentication.md) for the design context.

### Gemini CLI (`gemini-cli`)

Three auth modes, pick one.
The adapter checks them in this order and uses the first it finds; detection logic lives
in [src/metaproc/adapters/gemini.py:146](../../src/metaproc/adapters/gemini.py#L146).

**Mode 1 — Direct Gemini API (personal key).** One env var, no GCP needed.

| Variable | Required | Notes |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Get from https://aistudio.google.com/app/apikey |

```bash
export GEMINI_API_KEY=AIza...
```

**Mode 2 — Vertex AI Express (API-key-scoped Vertex).** Useful when you need Vertex
routing but don’t want to set up ADC.

| Variable | Required | Notes |
| --- | --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | Set to `true` to flip the adapter into Vertex AI mode |
| `GOOGLE_API_KEY` | Yes | Get from https://aistudio.google.com/app/apikey |

```bash
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_API_KEY=AIza...
```

**Mode 3 — Vertex AI + ADC (service-account / gcloud ADC).** **Recommended for this
repo** — reuses the GCP credentials operators already have for Batch dispatch and for
the `pi-cli` Vertex MaaS provider.
No Gemini-specific key needed.

| Variable | Required | Notes |
| --- | --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | Set to `true` to flip the adapter into Vertex AI mode |
| `GOOGLE_CLOUD_PROJECT` | Yes | Set to `$METAPROC_GCP_PROJECT` (already in `.env`) |

ADC itself comes from one of (in order): `GOOGLE_APPLICATION_CREDENTIALS` pointing at a
service-account key file, `GCP_CREDENTIALS_BASE64` (decoded at metaproc startup by
[gcp_credentials.py](../../src/metaproc/cloud/gcp/gcp_credentials.py)), or
`gcloud auth application-default login`.

```bash
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT="$METAPROC_GCP_PROJECT"
# Then: `gcloud auth application-default login` or set GCP_CREDENTIALS_BASE64.
```

The `smoke-adapter-gemini` process uses Mode 3 so the smoke is green on any operator
laptop already set up for Batch dispatch — see
[docs/arch/arch-testing.md](../arch/arch-testing.md) and
[process/self-test/smoke-adapter-gemini.process.md](../../process/self-test/smoke-adapter-gemini.process.md).

## GCP Cloud Infrastructure

These are the current GCP resources used by metaproc cloud execution.

| Resource | Value |
| --- | --- |
| GCP project | `exampletool` |
| Container image | `us-central1-docker.pkg.dev/exampletool/metaproc/agent:latest` |
| Filestore | `<filestore-ip>:/metaproc_runs` (1 TB) |
| Service account | `<worker-sa>@exampletool` |

Auth chain: `.env` contains `GCP_CREDENTIALS_BASE64` (see Vertex AI MaaS above).
metaproc’s CLI loads `.env` at startup (`cli.py:_load_dotenv`), which feeds into
`gcp_credentials.py` — decodes to a temp file and sets `GOOGLE_APPLICATION_CREDENTIALS`.
No manual auth setup is needed for metaproc commands.
`gcloud` CLI uses a separate auth chain (personal login or
`gcloud auth activate-service-account`).

Environment variables for cloud runs:

| Variable | Purpose |
| --- | --- |
| `METAPROC_GCP_PROJECT` | GCP project ID |
| `METAPROC_GCP_CONTAINER_IMAGE` | Container image for worker VMs |
| `METAPROC_GCP_FILESTORE_SERVER` | Filestore NFS server IP |
| `METAPROC_GCP_FILESTORE_MOUNT_PATH` | Filestore mount path (default: `/mnt/filestore`) |
| `METAPROC_GCP_SERVICE_ACCOUNT` | Service account email for Batch jobs. **Required** for any run that pulls `gh-token` from Secret Manager — without it, Batch uses the default Compute Engine SA and Secret Manager access fails with `PermissionDenied`. |
| `METAPROC_GCP_SECRET_GH_TOKEN` | Secret Manager resource name for GH_TOKEN (required when `GH_TOKEN` is set — plaintext fallback is refused) |
| `METAPROC_GCP_SECRET_CLAUDE_CREDS` | Secret Manager resource name for the Claude Code CLI Personal-Plan credential (required when dispatching `variant=claude-code-cli` via `--backend gcp-worker` with the subscription credential; see the Claude Code CLI adapter section above) |

`METAPROC_GCP_FILESTORE_MOUNT_PATH` controls where Batch VMs mount the share.
For local runs, authored specs see `RUNS_DIR` instead.
A workstation-mounted Filestore is not a supported cloud-orchestration path.

### ADC (Application Default Credentials)

On Batch worker and orchestrator VMs, auth uses ADC — the VM’s attached service account
provides credentials via the GCE metadata server.
**Do not set `GCP_CREDENTIALS_BASE64` on cloud VMs.** The credential bootstrap detects
the Batch environment via `BATCH_TASK_INDEX`. It also recognizes a configured Filestore
path only when that path is an actual mount, covering persistent GCP hosts without
letting configuration alone override local credential precedence.
In either case, it skips base64 decoding and uses ADC.

For local and CI runs, ADC resolution order:
1. `GOOGLE_APPLICATION_CREDENTIALS` (SA key file path)
2. `GCP_CREDENTIALS_BASE64` (base64-encoded SA key, decoded at startup)
3. `gcloud auth application-default login` (interactive)

### GH_TOKEN via Secret Manager

For private repo access on Batch VMs, `GH_TOKEN` is injected only via Secret Manager:

```bash
# Set once in shell profile or .env
export METAPROC_GCP_SECRET_GH_TOKEN=projects/exampletool/secrets/gh-token/versions/latest
```

Dispatch places only the Secret Manager version resource in the Batch job spec.
The worker or orchestrator fetches the value through the Secret Manager API under its
attached service account before bootstrap.
Do not switch this to Batch `secret_variables`: Batch agent logs can expose the expanded
container environment.
Submitting a Batch job with `GH_TOKEN` set but `METAPROC_GCP_SECRET_GH_TOKEN` unset is
refused up front to prevent plaintext leakage.

The Batch service account needs `roles/secretmanager.secretAccessor` on the `gh-token`
secret (and the Cloud Build builder SA needs it too if you build the cloud image in the
same project):

```bash
PROJECT=exampletool
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
BATCH_SA=<worker-sa>@${PROJECT}.iam.gserviceaccount.com
CLOUDBUILD_SA=${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com

for SA in "$BATCH_SA" "$CLOUDBUILD_SA"; do
  gcloud secrets add-iam-policy-binding gh-token \
    --project "$PROJECT" \
    --member "serviceAccount:${SA}" \
    --role roles/secretmanager.secretAccessor
done

# Verify
gcloud secrets get-iam-policy gh-token --project "$PROJECT"
```

Run once per project (or rerun after rotating service accounts).
If `gcloud secrets add-iam-policy-binding` fails with `PERMISSION_DENIED`, the caller
needs `roles/ secretmanager.admin` on the secret or project.

See [cloud dispatch](cloud-dispatch.runbook.md) for the framework-level Batch procedure.
Downstream repositories own their datasets, live process specs, and infrastructure
preflight.

## Run readiness check

Before a long run, verify the full stack:

```bash
# Check credentials + live API connectivity + run directory
metaproc auth-check \
  --live --variant <VARIANT> \
  --run-dir <PATH_TO_RUN_DIR>
```

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
