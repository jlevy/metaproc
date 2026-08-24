---
title: "Architecture: Authentication and Credentials"
description: Credential vehicles (A and B), the per-attempt slot lifecycle, the metaproc auth-pool, and how the system structurally prevents cross-account leakage.
author: metaproc team
status: Draft — partial currency notice below
---
# Architecture: Authentication and Credentials

**Date:** 2026-04-21 (last updated 2026-08-24) **Status:** Draft — partial currency
notice below

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `docs/arch/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-claude-code-harness](arch-claude-code-harness.md),
> [arch-testing](arch-testing.md).

## Currency notice (2026-04-28)

This doc was written 2026-04-21 against the original Vehicle B credential pool design.
The 2026-04-27 senior engineering review and the resulting Vehicle A pool redesign
(documented in this architecture) have shipped Phases 1-7, 10, and 11. Vehicle B remains
as an ongoing backup (no scheduled deprecation).
Sections **§N.14, §N.15, §N.16** (below) and the operator runbook
[`metaproc/docs/runbooks/credential-setup.runbook.md` § Claude Code CLI](../runbooks/credential-setup.runbook.md#claude-code-cli-claude-code-cli)
reflect the current design.

The bulk of this doc (§1 env-var registry, line numbers throughout, §5 adapter Protocol,
§6 `auth-check`, §N.1-§N.10) is **partially stale** in detail — the system architecture
is correct but cited line numbers, env-var lists, and code examples reflect pre-redesign
state. Trust the current sections of this architecture, the operator runbook, and the
source code over the older sections of this doc.
A full structural rewrite is filed as a follow-up.

**Quick map of what’s current vs.
stale**:

| Section | Currency | Authoritative source |
| --- | --- | --- |
| §N.16 (below) — Phase 10 typed payload cohorts | ✅ Current | This doc |
| §N.15 (below) — Phase 11 V-A end-to-end + V-B safe mode | ✅ Current | This doc |
| §N.14 — Vehicle A pool redesign | ✅ Current | This doc |
| §N.13 (failover semantics, 401/403 → RETRY_AFTER_WAIT) | ✅ Current | This doc |
| §N.12 (diagnostic preservation Protocol) | ✅ Current | This doc |
| §N.11 (Keychain divergence empirics) | ✅ Mostly current; long-term-options re-ranked in research §F7 | Research doc |
| §N.1-§N.10 (pool surface) | Mostly current; line numbers stale | Redesign spec + source |
| §1 env-var registry | Stale — missing METAPROC_AUTH_* group, CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CONFIG_DIR | Source: `src/metaproc/config/env_vars.py` |
| §5 adapter Protocol | Stale — `AuthCapableCliAdapter` shape extended in Phase 2 (vehicle/blob kwargs) and Phase 7 (`setup_token_command`) | Source: `src/metaproc/adapters/base.py` |
| Line numbers throughout | Stale (50-300 lines off post-review) | Source files |

## Overview

Technical reference for every authentication and credential path used by metaproc across
local development, cloud dispatch (GCP Batch orchestrator + workers,
`metaproc gcp run`), and every model backend (`claude-code-cli`, `pi-cli`,
`gemini-cli`). It catalogs the env-var registry, the GCP credential resolution chain,
the Claude Code CLI Personal-Plan OAuth two-hop flow, Pi CLI provider auth (including
Vertex MaaS token injection), the Secret Manager registry for Batch jobs, and the
`auth-check` verification command.

Primary sources: [metaproc/docs/arch/arch-metaproc-core.md](arch-metaproc-core.md),
[metaproc/docs/arch/arch-cloud-execution.md](arch-cloud-execution.md),
[metaproc/docs/runbooks/credential-setup.runbook.md](../runbooks/credential-setup.runbook.md),
and the code paths referenced inline.

## Goals and Non-Goals

### Goals

- Document every credential type, how it enters the process, and how it reaches the
  component that consumes it.
- Make precedence rules for each credential explicit (what wins when multiple modes are
  set).
- Map every relevant env var to its consumer and to the module where it is read.
- Document the anti-leakage invariant (`resolve_gcp_secret_ref`) and the two-hop
  Keychain → Secret Manager → adapter-bootstrap flow for Claude Code Personal Plan.

### Non-Goals

- Operator setup walkthroughs — those live in
  [metaproc/docs/runbooks/credential-setup.runbook.md](../runbooks/credential-setup.runbook.md)
  and the cloud dispatch runbook.
- Model cost / routing policy (covered in the runtime-roles memory and research-run
  docs).
- Non-metaproc auth (the example-tool site, IDE sign-in, etc.).

## System Context

Credentials flow through four surfaces:

1. **Operator shell / `.env` file** — plaintext API keys and a base64 GCP service
   account key. Loaded at Typer startup by
   [src/metaproc/cli.py:53](../../src/metaproc/cli.py#L53) (`_load_dotenv`).
2. **macOS Keychain** — the Claude Code Personal-Plan OAuth blob.
   Read by `metaproc claude-auth push` and pushed to GCP Secret Manager.
3. **GCP Secret Manager** — the source of truth for every credential delivered to a
   Batch job. Names are registered in `GCP_SECRET_REFS` and bound by the Batch service
   via `Environment.secret_variables`.
4. **GCP service account ADC** — used on Batch worker and orchestrator VMs via the
   attached SA and GCE metadata server.

```
┌────────────────┐    ┌────────────────┐    ┌────────────────────┐
│ Laptop .env    │    │ macOS Keychain │    │ GCP Secret Manager │
│ (API keys,     │    │ (Claude Code   │    │ (gh-token,         │
│  GCP_CREDS_B64)│    │  OAuth blob)   │    │  claude-code-creds)│
└────────┬───────┘    └────────┬───────┘    └─────────┬──────────┘
         │                     │                      │
         │ _load_dotenv()      │ claude-auth push     │ Batch secret_variables
         ▼                     ▼                      ▼
   ┌─────────────────────────────────────────────────────────┐
   │                     os.environ                           │
   │  (masked via SECRET_VARS in `metaproc env`)              │
   └────────┬────────────────────────────────────────────────┘
            │
            ▼
   ┌─────────────────────────────────────────────────────────┐
   │   MetaprocEnv enum (metaproc.config.env_vars)            │
   │   Typed accessors: .read_str / .read_int / .read_path    │
   └────────┬────────────────────────────────────────────────┘
            ├──► gcp_credentials.py       (ADC + token refresh)
            ├──► adapters/*.py            (check_auth, prepare_env, bootstrap)
            ├──► cloud/gcp/batch_backend  (GCP_SECRET_REFS registry)
            ├──► worker_dispatch.py       (Batch job env + secret_variables)
            └──► commands/auth_check.py   (Phase 1/2/2b/3 verification)
```

## Design

### Components

#### 1. MetaprocEnv — typed env-var registry

**File:** [src/metaproc/config/env_vars.py](../../src/metaproc/config/env_vars.py)

Every env var read anywhere in metaproc flows through one enum member declared with a
factory (`real`, `tunable`, `secret`, `optional`) carrying metadata (kind, description,
example). Empty strings and the literal `"changeme"` are treated as unset.
The factories live in
[src/metaproc/config/env_enum.py](../../src/metaproc/config/env_enum.py).

**Auth-relevant secret members**
([env_vars.py:155-186](../../src/metaproc/config/env_vars.py#L155-L186)):

| Env Var | Kind | Consumers |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | `secret` | `claude-code-cli` (API-key mode), `pi-cli` (anthropic provider) |
| `OPENAI_API_KEY` | `secret` | `pi-cli` (openai provider) |
| `GEMINI_API_KEY` | `secret` | `gemini-cli` (direct API), `pi-cli` (google provider) |
| `GOOGLE_API_KEY` | `secret` | `gemini-cli` (Vertex AI Express fallback) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `optional` | `gemini-cli` mode switch |
| `GOOGLE_CLOUD_PROJECT` | `optional` | `pi-cli` (vertex-ai provider detection), Vertex SDK routing |
| `PERPLEXITY_API_KEY` | `secret` | Tool wrapper web-search provider |
| `GH_TOKEN` | `secret` | Git credential helper on Batch containers (not read on laptops) |
| `GH_PROMPT_DISABLED` | `real` | `gh` CLI — disables interactive prompts |
| `GCP_CREDENTIALS_BASE64` | `secret` | `gcp_credentials.py` (laptop/CI ADC bootstrap) |
| `GOOGLE_APPLICATION_CREDENTIALS` | `optional` | Standard GCP ADC path-to-key-file |
| `CLAUDE_CODE_CREDS_JSON` | `secret` | Injected on Batch workers from Secret Manager; consumed by `ClaudeCodeCliAdapter.bootstrap()` |
| `METAPROC_GCP_SECRET_GH_TOKEN` | `optional` | Secret Manager ref for `GH_TOKEN` |
| `METAPROC_GCP_SECRET_CLAUDE_CREDS` | `optional` | Secret Manager ref for `CLAUDE_CODE_CREDS_JSON` |
| `METAPROC_GCP_SERVICE_ACCOUNT` | `tunable` | SA email attached to Batch VMs (Secret Manager access depends on it) |

`SECRET_VARS` ([env_vars.py:234-247](../../src/metaproc/config/env_vars.py#L234-L247))
is a `frozenset[MetaprocEnv]` — its members are masked by `metaproc env` and similar
introspection outputs.
Membership is explicit (not name-guessed): the `METAPROC_GCP_SECRET_*` refs are included
because they leak project structure, even though they are not themselves secrets.

#### 2. `.env` loading at CLI startup

**File:** [src/metaproc/cli.py:53](../../src/metaproc/cli.py#L53) (`_load_dotenv`)

Walks upward from the current working directory to locate the nearest `.env`. Injects
`KEY=VALUE` lines into `os.environ` via `os.environ.setdefault()` — the existing shell
environment always wins over `.env`. Called from the Typer root callback so every
subcommand sees the loaded vars.

This is how laptop runs pick up `ANTHROPIC_API_KEY`, `GCP_CREDENTIALS_BASE64`,
`PERPLEXITY_API_KEY`, and the `METAPROC_GCP_*` cloud config.

#### 3. GCP credential resolution (`gcp_credentials.py`)

**File:**
[src/metaproc/cloud/gcp/gcp_credentials.py](../../src/metaproc/cloud/gcp/gcp_credentials.py)

The module owns initialization of a `google.auth` credentials object with the
`https://www.googleapis.com/auth/cloud-platform` scope, and provides
`get_access_token()` with a 10-minute proactive refresh margin
(`GCP_TOKEN_REFRESH_MARGIN_MINUTES` from `settings.py`). Thread-safe via a module-level
`threading.Lock`.

Resolution order (standard `google.auth.default()` chain, with one metaproc pre-step):

1. `GOOGLE_APPLICATION_CREDENTIALS` env var — path to an SA key JSON. Respected verbatim
   if set.
2. `GCP_CREDENTIALS_BASE64` env var — base64-encoded SA key.
   Decoded to `${TMPDIR}/gcp/keys/sa-key.json` at mode 0600, then
   `GOOGLE_APPLICATION_CREDENTIALS` is set to point at it
   ([gcp_credentials.py:66](../../src/metaproc/cloud/gcp/gcp_credentials.py#L66)
   `_bootstrap_credentials_from_base64`).
3. GCE metadata server — used automatically on GCP VMs.
4. User ADC from `gcloud auth application-default login` — final fallback.

**Attached-identity suppression**
([gcp_credentials.py](../../src/metaproc/cloud/gcp/gcp_credentials.py)
`_should_prefer_attached_gcp_identity`): the base64 decode path is skipped when either a
non-empty `BATCH_TASK_INDEX` proves GCP Batch execution or a configured Filestore path
is an actual mounted filesystem.
A configured Filestore server alone is insufficient.
This preserves the attached service account on Batch and persistent GCP hosts instead of
silently replacing it with a stale base64 credential.

**Token refresh**
([gcp_credentials.py:109](../../src/metaproc/cloud/gcp/gcp_credentials.py#L109)
`get_access_token`): refreshes if `not creds.valid` or expiry is within the refresh
margin.
Logs the token fingerprint (first 8 hex of SHA-256) to correlate without leaking.

**Related module:**
[src/metaproc/cloud/gcp/resolve_token.py](../../src/metaproc/cloud/gcp/resolve_token.py)
exposes `resolve_gcp_token()` — thin wrapper called by pi-cli vertex-maas injection,
`auth-check`, and `run_parallel` batch boundaries.

#### 4. Secret Manager registry (`GCP_SECRET_REFS`)

**File:**
[src/metaproc/cloud/gcp/batch_backend.py:68](../../src/metaproc/cloud/gcp/batch_backend.py#L68)

```python
GCP_SECRET_REFS: tuple[tuple[str, str, str], ...] = (
    ("GH_TOKEN", "METAPROC_GCP_SECRET_GH_TOKEN", "plaintext GitHub token"),
    ("CLAUDE_CODE_CREDS_JSON", "METAPROC_GCP_SECRET_CLAUDE_CREDS", "Claude Code OAuth blob"),
)
```

Every credential delivered to a Batch job flows through this table.
Each row is `(plaintext_env, secret_env, description)`.

`resolve_gcp_secret_ref()`
([batch_backend.py:78](../../src/metaproc/cloud/gcp/batch_backend.py#L78)) enforces the
anti-leakage invariant:

- If the Secret Manager ref env var is set → return its value (the Secret Manager
  resource name).
- Else if the plaintext env var is set → raise `RuntimeError`. Dispatch fails rather
  than embedding plaintext in the Batch job spec (where `gcloud batch jobs describe`
  would expose it).
- Else → return `""`.

`_build_secret_env_vars()` walks the registry once and returns the
`{plaintext_env: secret_resource_name}` mapping that becomes
`Environment.secret_variables` on the Batch task.
Consumed by both `worker_dispatch.py`
([lines 577-579](../../src/metaproc/cloud/gcp/worker_dispatch.py#L577-L579)) and
`orchestrator_dispatch.py`
([lines 200-204](../../src/metaproc/cloud/gcp/orchestrator_dispatch.py#L200-L204)).

**Adding a new credential** is a one-line change: append a row to `GCP_SECRET_REFS`,
then consume the plaintext env var in the code path that needs it (typically the
corresponding adapter’s `bootstrap(home)` hook).
No dispatch or policy wiring is required.

#### 5. Adapter protocol — auth-relevant methods

**File:** [src/metaproc/adapters/base.py:44](../../src/metaproc/adapters/base.py#L44)

Each adapter implements three auth-relevant methods:

- `check_auth() -> AuthStatus` — surfaces whether the CLI binary exists and whether
  credentials are discoverable.
  Consumed by `auth-check` Phase 1.
- `prepare_env(env, merged_config) -> dict[str, str]` — transforms the subprocess
  environment. Typically just filters stray vars and sets perf tweaks
  (`PI_SKIP_VERSION_CHECK=1`, removal of inherited `CLAUDECODE`).
- `bootstrap(home: Path) -> None` — materializes per-task filesystem state such as
  credentials files. Default is no-op.
  Called once per container by `worker_entrypoint.py` / `orchestrator_entrypoint.py` /
  `gcp_run_entrypoint.py`.

The adapter registry
([src/metaproc/adapters/registry.py](../../src/metaproc/adapters/registry.py)) holds
`claude-code-cli`, `gemini-cli`, and `pi-cli` instances.

#### 6. `auth-check` command (Phase 1 / 2 / 2b / 3)

**File:**
[src/metaproc/commands/auth_check.py](../../src/metaproc/commands/auth_check.py)

The single operator-facing verification tool:

- **Phase 1 — environment and credential audit**
  ([auth_check.py:489](../../src/metaproc/commands/auth_check.py#L489)). Always runs.
  Checks:
  - Disk space (`engine.preflight.check_disk_space`).
  - `_check_gcloud_token()` — resolves a GCP token via `resolve_gcp_token()` and reports
    length + expiry. Non-fatal when `metaproc[gcp]` extra is not installed.
  - Each registered adapter’s `check_auth()` — binary on PATH + credential
    discoverability. Scoped to one adapter when `--variant` is passed.
- **Phase 2 — `--live`**
  ([auth_check.py:514](../../src/metaproc/commands/auth_check.py#L514)). Writes a tiny
  prompt (`"Respond with exactly: OK"`) to a tempfile and invokes each adapter’s
  `build_command()` under its own `prepare_env()`. For `pi-cli`, injects a fresh GCP
  token as `--api-key`
  ([auth_check.py:235](../../src/metaproc/commands/auth_check.py#L235)) so `vertex-maas`
  is exercised with live auth.
  Includes a `pi --list-models` registration gate
  ([auth_check.py:247](../../src/metaproc/commands/auth_check.py#L247)) to prevent
  pi-cli from silently falling back to its default provider and false-greening the check
  (the Phase 0c blocker).
- **Phase 2b — `--claude-secret-ref` / `$METAPROC_GCP_SECRET_CLAUDE_CREDS`**
  ([auth_check.py:538](../../src/metaproc/commands/auth_check.py#L538)). Fetches a
  Claude Code OAuth blob from Secret Manager, validates the payload shape, writes it to
  a throwaway `HOME/.claude/.credentials.json`, unsets inherited `ANTHROPIC_API_KEY`,
  and runs `claude -p` against it.
  This is the end-to-end check for the cloud path Batch workers use.
- **Phase 3 — `--run-dir`**
  ([auth_check.py:552](../../src/metaproc/commands/auth_check.py#L552)). Confirms the
  run directory exists, that a `progress.md` with an items list is readable, and that
  the dir is writable.

Key helpers:

- `_resolve_variant_target(variant)` — splits a variant like `pi-cli-glm-5-maas` into
  `(adapter_type, model_name)`. Unknown prefixes fall back to `pi-cli`.
- `_infer_pi_provider(model_name)` — maps `*-maas` → `vertex-maas`, `gemini-*` /
  `google/gemini*` → `google-vertex`, `claude-*` → `anthropic`, etc.
  Prevents false-greens when the operator specifies only a model.
- `_run_claude_secret_live_check(secret_ref)` — validates that the Secret Manager
  payload still works when materialized into an isolated Claude home directory.

Exit code is `0` on full pass, `1` on any failure.

#### 7. `claude-auth` command — Keychain ↔ Secret Manager

**File:**
[src/metaproc/commands/claude_auth.py](../../src/metaproc/commands/claude_auth.py)

Manages the Claude Code Personal-Plan OAuth blob lifecycle on macOS. Three subcommands:

- `push` — reads `Claude Code-credentials` Keychain item via
  `security find-generic-password -w`, validates it contains a `claudeAiOauth` top-level
  key, pipes it to `gcloud secrets versions add --data-file=-`. Grants
  `roles/secretmanager.secretAccessor` to the Batch SA (`user@example.invalid` by
  default) on first use.
  Secret name: `claude-code-creds-<username>`.
- `show` — prints secret metadata and IAM policy (never the payload).
- `rotate` — pushes a new version, then destroys all prior enabled versions.

**Zero-plaintext-on-disk invariant:** the Keychain payload never lands in a temp file —
it streams from Keychain → stdin → gcloud.
Constants: `KEYCHAIN_SERVICE = "Claude Code-credentials"`,
`DEFAULT_BATCH_SA = "user@example.invalid"`.

### Auth flows by use case

#### UC-1: Laptop run with `ANTHROPIC_API_KEY` (pay-per-token Claude)

1. Operator exports `ANTHROPIC_API_KEY=sk-ant-...` (shell or `.env`).
2. `_load_dotenv()` seeds `os.environ`.
3. `ClaudeCodeCliAdapter.check_auth()`
   ([claude_code.py:169](../../src/metaproc/adapters/claude_code.py#L169)) sees the key
   → reports `auth_mode="api-key"`.
4. `claude -p @<prompt> ...` invoked as a subprocess; the Claude Code CLI reads
   `ANTHROPIC_API_KEY` directly from its own env.
5. `prepare_env` strips `CLAUDECODE` (a harness-inherited var that confuses nested
   invocations).

#### UC-2: Laptop run with Personal Plan (interactive login)

1. Operator has previously run `claude login`; the CLI stored OAuth tokens in
   `~/.claude/.credentials.json` (and/or the macOS Keychain, depending on the CLI
   version).
2. `check_auth()` sees `~/.claude/` exists and no `ANTHROPIC_API_KEY` → reports
   `auth_mode="interactive-login"`.
3. The CLI subprocess uses its stored credential directly.

**Precedence trap:** if both `ANTHROPIC_API_KEY` and `~/.claude/.credentials.json`
exist, the Claude Code CLI silently prefers the API key.
On Personal-Plan workers this bypasses the subscription and bills per-token.
`ANTHROPIC_API_KEY` **MUST NOT be set** in that topology.

#### UC-3: GCP Batch worker with Personal-Plan Claude (two-hop)

This is the sanctioned production path; see
[credential-setup.runbook.md](../runbooks/credential-setup.runbook.md) and
[cloud-dispatch.runbook.md](../runbooks/cloud-dispatch.runbook.md#4a-gcp-batch-personal-plan).

```
macOS Keychain                                  ┐
  │ security find-generic-password -w           │ one-time per user
  ▼                                              │
[stdin pipe]                                     │
  │ gcloud secrets versions add --data-file=-    │
  ▼                                              │
GCP Secret Manager: claude-code-creds-<user>    ┘

Dispatch time (per Batch job):
  METAPROC_GCP_SECRET_CLAUDE_CREDS
  → resolve_gcp_secret_ref()
  → Batch Environment.secret_variables["CLAUDE_CODE_CREDS_JSON"] = ref
  → Batch service resolves ref at task start
  → container env has CLAUDE_CODE_CREDS_JSON=<json-blob>

Container bootstrap (once per task):
  worker_entrypoint.main()
  → bootstrap_container()      # git, pi models.json, plugins
  → for adapter in ADAPTER_REGISTRY.values(): adapter.bootstrap(home)
  → ClaudeCodeCliAdapter.bootstrap(home):
       creds_json = os.environ.pop("CLAUDE_CODE_CREDS_JSON")
       validate JSON, require "claudeAiOauth" key
       home/.claude/.credentials.json  (mode 0600, dir 0700)
  → CLAUDE_CODE_CREDS_JSON no longer in env for any child process
```

Claude Code CLI then reads its credential file normally.
`ANTHROPIC_API_KEY` must stay unset (dispatch does not inject it).

Reference: [claude_code.py:227](../../src/metaproc/adapters/claude_code.py#L227)
(`bootstrap()`), [credential-setup.runbook.md](../runbooks/credential-setup.runbook.md)
§ Claude Code CLI, [arch-cloud-execution.md §3.10](arch-cloud-execution.md).

#### UC-4: Laptop run with Pi CLI (Anthropic / OpenAI / direct Google)

1. `check_auth()` checks `~/.pi/agent/auth.json` first (from `pi /login`); if present,
   reports `auth-json` mode.
2. Otherwise scans for provider API keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `GEMINI_API_KEY`, `GOOGLE_CLOUD_PROJECT` (for vertex-ai).
   Reports which are found.
3. Pi CLI subprocess reads the relevant env var itself — metaproc does not transform it.
4. `prepare_env` sets `PI_SKIP_VERSION_CHECK=1` to avoid a startup HTTP call.

**Pi valid providers** (validated by `validate_config`): anthropic, openai, google,
vertex, azure, bedrock, plus custom providers registered in `models.json`
(`vertex-maas`, `google-vertex`).

#### UC-5: Laptop run with Pi CLI + Vertex MaaS (GLM-5, Kimi K2, DeepSeek, etc.)

Vertex MaaS (Model-as-a-Service) exposes an OpenAI-compatible endpoint at
`https://aiplatform.googleapis.com/v1/projects/<PROJECT>/locations/global/endpoints/openapi`.
It requires a **GCP access token** as the bearer credential.

Auth flow:

1. Pi CLI loads `~/.pi/agent/models.json`. Metaproc dispatch (`build_pi_models_json` at
   [batch_backend.py:277](../../src/metaproc/cloud/gcp/batch_backend.py#L277)) prefers
   operator-authored config and falls back to the packaged default
   [src/metaproc/data/pi-models.default.json](../../src/metaproc/data/pi-models.default.json).

2. The `vertex-maas` provider is declared with `"apiKey": "<injected-by-metaproc>"` and
   `"authHeader": true` → Pi sends `Authorization: Bearer <apiKey>`.

3. Metaproc resolves a GCP access token **once per batch** (not per item) via
   `resolve_gcp_token()`:

   ```python
   # src/metaproc/commands/run_parallel.py:635
   def _refresh_gcloud_token() -> None:
       if not _needs_gcloud_token:
           return
       from metaproc.cloud.gcp.resolve_token import resolve_gcp_token
       merged_config["api_key"] = resolve_gcp_token()
   ```

   Equivalent logic lives in `run_process.py` at line 1019. The guard
   `_needs_gcloud_token` is `True` when `adapter_type == "pi-cli"` and
   `provider.startswith("vertex")` and `backend == "local"`.

4. The token is injected into the adapter’s `api_key` merged config and passed to the
   pi-cli subprocess as `--api-key <token>`
   ([auth_check.py:235](../../src/metaproc/commands/auth_check.py#L235) follows the same
   pattern for live checks).

5. Tokens auto-refresh via `google.auth` before expiry — no subprocess shell-outs, no
   TTL guessing. Failure raises; there is no degraded fallback.

The underlying GCP credentials come from whichever rung of the `gcp_credentials.py`
chain resolved (laptop: typically `GCP_CREDENTIALS_BASE64`).

#### UC-6: GCP Batch worker with Pi CLI + Vertex MaaS

Same logical flow as UC-5, but the source of the GCP token is ADC via the attached SA
(`METAPROC_GCP_SERVICE_ACCOUNT`, usually `<worker-sa>`), not a base64 blob.

Additional per-container rewrites
([batch_backend.py:249](../../src/metaproc/cloud/gcp/batch_backend.py#L249)
`_rewrite_pi_models_json`):

- `apiKey` values beginning with `!gcloud` (a shell-command token that Pi expands) are
  rewritten to `!gcp-access-token.sh` — a small helper installed in the container that
  prints an ADC token.
  Containers don’t have the `gcloud` CLI.
- Vertex `baseUrl` project paths are normalized to the dispatch project ID so
  operator-local models.json works verbatim across projects.

`GOOGLE_CLOUD_LOCATION=global` is set on every worker/orchestrator to satisfy the Gemini
3.x preview SDK requirement.

#### UC-7: Pi CLI with `google-vertex` provider (first-party Gemini on Vertex)

Distinct from `vertex-maas`. Uses Pi’s native `google-vertex` API (`@google/genai` SDK)
instead of the OpenAI-compatible path.

- `"apiKey": "gcp-vertex-credentials"` — magic string that tells the Pi SDK to use ADC
  rather than an explicit token.
- Requires `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` in env.
- Laptop: ADC via `GCP_CREDENTIALS_BASE64`. Container: ADC via attached SA.

#### UC-8: Gemini CLI adapter (`gemini-cli`)

**File:**
[src/metaproc/adapters/gemini.py:146](../../src/metaproc/adapters/gemini.py#L146)

Modes:
- `GEMINI_API_KEY` set → direct API mode.
- `GOOGLE_GENAI_USE_VERTEXAI` truthy → Vertex AI mode; uses `GOOGLE_API_KEY` if present
  (Vertex AI Express), otherwise `GOOGLE_CLOUD_PROJECT` + ADC.

Used much less than Pi / Claude Code in current runs.

#### UC-9: `GH_TOKEN` for private-repo clone in containers

1. Operator exports
   `METAPROC_GCP_SECRET_GH_TOKEN=projects/<proj>/secrets/gh-token/versions/latest`.
2. Dispatch: `resolve_gcp_secret_ref()` puts it in
   `Environment.secret_variables["GH_TOKEN"]`.
3. Container: `bootstrap_container()`
   ([container_bootstrap.py:101](../../src/metaproc/cloud/gcp/container_bootstrap.py#L101))
   configures a git credential helper that injects the token into HTTPS clones.
4. No plaintext `GH_TOKEN` ever appears in the Batch job spec.
   Setting `GH_TOKEN` locally without the ref env var → dispatch refuses to submit.

IAM bootstrap: both the Batch SA (`<worker-sa>`) and the Cloud Build builder SA need
`roles/secretmanager.secretAccessor` on the `gh-token` secret.
See
[credential-setup.md § GH_TOKEN via Secret Manager](../runbooks/credential-setup.runbook.md#gh_token-via-secret-manager).

#### UC-10: `metaproc gcp run` arbitrary commands

**File:**
[src/metaproc/cloud/gcp/gcp_run_entrypoint.py](../../src/metaproc/cloud/gcp/gcp_run_entrypoint.py)

Submits a Batch job that runs an arbitrary command (e.g., `python -m my_package.task`)
with the dispatcher’s current metaproc + repo state.
Shares `batch_backend.py` and the `GCP_SECRET_REFS` registry with worker dispatch, so
credentials behave identically:

- Attached SA via `METAPROC_GCP_SERVICE_ACCOUNT`.
- `secret_variables` populated by the same `_build_secret_env_vars()` call.
- Entrypoint calls `adapter.bootstrap(home)` for every registered adapter, so
  `CLAUDE_CODE_CREDS_JSON` is materialized even when the user command is a
  package-specific CLI rather than `run-parallel`.

No orchestrator lease, no claim registry — this path is for one-shot invocations.

### Data Model

#### AuthStatus

**File:** [src/metaproc/adapters/base.py](../../src/metaproc/adapters/base.py)
(`AuthStatus` dataclass)

```python
@dataclass(frozen=True)
class AuthStatus:
    adapter_type: str
    cli_found: bool
    cli_path: str | None
    credentials_found: bool
    auth_mode: str          # "api-key" | "interactive-login" | "auth-json" | "api-key (...)" | "none"
    details: str
    setup_hint: str
```

Returned by every adapter’s `check_auth()`. Consumed by `auth-check` and `metaproc env`
introspection output.

#### GCPBatchConfig

**File:**
[src/metaproc/cloud/gcp/batch_backend.py](../../src/metaproc/cloud/gcp/batch_backend.py)

Frozen dataclass carrying everything needed to submit a Batch job.
Auth-relevant fields: `project`, `service_account_email`, `secret_env_vars` (built via
`_build_secret_env_vars()`), `filestore_*` (NFS network-level access, no per-request
auth).

### Interfaces

#### External systems touched

| System | Credential | Used by |
| --- | --- | --- |
| Anthropic API | `ANTHROPIC_API_KEY` | `claude-code-cli` (API mode), `pi-cli` (anthropic) |
| Anthropic OAuth (Personal Plan) | `~/.claude/.credentials.json` | `claude-code-cli` (subscription) |
| OpenAI API | `OPENAI_API_KEY` | `pi-cli` (openai) |
| Google AI Studio | `GEMINI_API_KEY` | `gemini-cli`, `pi-cli` (google) |
| GCP Vertex AI (MaaS) | GCP access token | `pi-cli` (vertex-maas) |
| GCP Vertex AI (native Gemini) | ADC | `pi-cli` (google-vertex), `gemini-cli` (Vertex mode) |
| GCP Secret Manager | ADC | Batch runtime (resolves `secret_variables`) |
| GCP Batch API | ADC | `cloud.gcp.worker_dispatch`, `orchestrator_dispatch`, `gcp_run_dispatch` |
| GCP Filestore (NFS) | network-level (SA needs VPC) | all cloud VMs |
| GCP Cloud Storage | ADC | `container_bootstrap._download_from_gcs` (wheel/workspace) |
| GitHub (private clone) | `GH_TOKEN` | container-level git credential helper |
| Perplexity | `PERPLEXITY_API_KEY` | Tool wrapper web search |

#### Internal contracts

- `Adapter.check_auth() -> AuthStatus` — the only auth-verification contract the rest of
  the system depends on.
- `Adapter.bootstrap(home) -> None` — the container-side contract for materializing
  credential files that are unsafe to keep as env vars for the job lifetime.
- `GCP_SECRET_REFS` + `resolve_gcp_secret_ref()` — the only sanctioned path for
  delivering credentials to a Batch job.

## Trade-offs and Alternatives

### Decision 1: Typed `MetaprocEnv` enum, not scattered `os.getenv` calls

**Chosen approach:** every env-var read goes through an enum member with a factory
declaration.

**Alternatives considered:**
- Scattered `os.getenv("FOO", default)` — rejected because drift is unavoidable and
  secret-masking becomes per-site.
- Pydantic `BaseSettings` — rejected because the coverage story (one registry, template
  export via `metaproc env --template`) benefits from a flat enum.

**Rationale:** single source of truth for docs, `.env.example` generation, secret
masking (`SECRET_VARS`), and `metaproc env` introspection.
A coverage test (in-progress in the env-var-registry-hardening plan) will enforce that
no direct `os.getenv` survives.

### Decision 2: Secret Manager required for every Batch-injected credential

**Chosen approach:** `GCP_SECRET_REFS` registry with hard-fail `resolve_gcp_secret_ref`.

**Alternatives considered:**
- Plaintext env vars on Batch — rejected: `gcloud batch jobs describe` returns env vars
  in the job spec.
- Per-credential handling — rejected: bespoke wiring per secret is what we replaced when
  generalizing from GH_TOKEN-only (rev3 of cloud-design).

**Rationale:** one policy, uniformly enforced.
Adding a secret is one line.

### Decision 3: GCP ADC on cloud VMs, base64 blob on laptops

**Chosen approach:** `_should_prefer_attached_gcp_identity()` skips
`GCP_CREDENTIALS_BASE64` decoding when a non-empty `BATCH_TASK_INDEX` proves GCP Batch
execution or when a configured Filestore path is an actual mounted filesystem.

**Alternatives considered:**
- Ship the base64 blob to cloud VMs via Secret Manager — rejected: the attached SA
  already provides ADC, and ADC auto-refreshes without metaproc involvement.
- Require operators to unset `GCP_CREDENTIALS_BASE64` on cloud VMs — rejected: fragile
  across orchestrator/worker env inheritance.

**Rationale:** Batch workers and persistent GCP hosts should keep ADC from their
attached service account.
Requiring an actual mount, rather than Filestore configuration alone, prevents a local
`.env` file from claiming cloud-runtime precedence.

### Decision 4: Adapter `bootstrap(home)` for credential materialization

**Chosen approach:** adapters own the conversion from `{PLAIN_ENV}` to
`~/.config/.../.credentials.json`, and they pop the env var after writing.

**Alternatives considered:**
- Container bootstrap writes credential files directly — rejected: the bootstrap would
  need adapter-specific knowledge; the adapter is the correct owner.
- Keep `CLAUDE_CODE_CREDS_JSON` in env for the whole job — rejected: child processes
  (subprocess + background tools) would inherit the OAuth blob.

**Rationale:** section 2.8 of arch-cloud-execution.md (container bootstrap contract)
codifies the hook.
Claude Code CLI is the current user; any future adapter with a similar
requirement plugs in without touching the dispatch layer.

### Decision 5: GCP token injection per-batch, not per-item

**Chosen approach:** `_refresh_gcloud_token()` runs once per batch boundary and stores
the token in the adapter’s `merged_config["api_key"]`.

**Alternatives considered:**
- Per-item token resolution — rejected: ~40x overhead for 500-item mines, makes logs
  noisier for no benefit (google.auth auto-refreshes within a window).
- Pi’s `!gcloud ...` shell-command token source on laptops — rejected: depends on
  `gcloud` CLI being on PATH and introduces a per-spawn subprocess.

**Rationale:** `google.auth` refreshes proactively.
Batch boundary is the right cadence.

## Security Considerations

### Authentication approach

- **Laptops:** env-var / file-based API keys or base64 SA key.
  The operator is responsible for `.env` hygiene; `.env` is git-ignored.
- **Cloud VMs:** ADC via attached service account.
  No long-lived key material on disk.
- **Claude Code Personal Plan:** zero plaintext on disk between Keychain and Secret
  Manager; credentials are materialized 0600 on the worker and the env var is popped
  before any child subprocess runs.
- **Batch jobs:** every injected credential comes from Secret Manager via
  `secret_variables`; `resolve_gcp_secret_ref` refuses plaintext fallthrough.

### Authorization model

- Batch SA (`user@example.invalid`) needs:
  - `roles/secretmanager.secretAccessor` on each registered secret (`gh-token`,
    `claude-code-creds-<user>`).
  - Batch job execution roles on the project.
  - `roles/storage.objectViewer` on the wheel/workspace GCS paths.
  - VPC attachment for Filestore.
- Operators running `metaproc gcp ...` locally need their own ADC with equivalent GCP
  permissions on the project.
- `claude-auth push` grants `secretmanager.secretAccessor` to the Batch SA on each
  `push` (idempotent).

### Data protection measures

- Secret values are masked by `SECRET_VARS` in `metaproc env` and other introspection
  outputs.
- GCP token log lines record only the SHA-256 fingerprint (first 8 hex).
- `CLAUDE_CODE_CREDS_JSON` is popped from `os.environ` after
  `ClaudeCodeCliAdapter.bootstrap()` writes the credential file, so the OAuth blob does
  not propagate to child processes.
- `~/.claude/` dir is forced to 0700; the credentials file to 0600.
- `claude-auth` uses `security -w` + stdin pipe — payload never touches a named
  tempfile.
- `GCP_CREDENTIALS_BASE64` is decoded to `${TMPDIR}/gcp/keys/sa-key.json` at 0600. This
  tempfile persists until system cleanup but is not world-readable.
- Batch `secret_variables` bindings keep plaintext out of `gcloud batch jobs describe`
  output.

### Known leakage risks

- `.env` on a shared filesystem — the operator’s responsibility.
  `.env` is git-ignored; no metaproc code writes to it.
- `GCP_CREDENTIALS_BASE64` value visible to any process on the laptop that can read the
  dev shell’s env — standard Unix posture.
- `GOOGLE_APPLICATION_CREDENTIALS` tempfile persists in `$TMPDIR` after process exit;
  0600 mode limits blast radius to the owning user.
- `auth-check --live` makes real API calls — will consume a small amount of quota /
  credit on each provider tested.

## Operational Concerns

### Verification

- `uv run metaproc auth-check` — Phase 1: credential audit; always run it first.
- `uv run metaproc auth-check --live --variant <V>` — end-to-end test against the target
  provider.
- `uv run metaproc auth-check --claude-secret-ref <projects/.../secrets/.../versions/latest>`
  — end-to-end test for the Secret Manager Claude credential path that Batch workers
  use.
- `uv run metaproc auth-check --live --variant <V> --run-dir <path>` — pre-flight before
  a long run.
- `uv run metaproc env --only-set` — shows every env var currently visible to metaproc,
  with secrets masked.
- `uv run metaproc claude-auth show` — confirms the Keychain secret is healthy and the
  Batch SA has accessor role.

### Monitoring

- GCP token refreshes are logged at INFO with SHA-256 fingerprint (to correlate token
  rotations without leaking).
- Credential initialization (`GCP credentials initialized: <CredsClassName>`) is logged
  once per process at INFO.
- `auth-check` Phase 2 records per-provider latency.

### Rotation / incident response

- **Anthropic API key rotation:** rotate in Anthropic console, update `.env` and/or
  `$ANTHROPIC_API_KEY`. No metaproc change.
- **Claude Code Personal Plan rotation:** run `claude login` on the Mac, then
  `metaproc claude-auth rotate` — pushes new version and destroys prior enabled versions
  in one step.
- **GCP SA key rotation:** regenerate key, base64-encode, update
  `GCP_CREDENTIALS_BASE64` in `.env`. Restart dev shell.
- **GH_TOKEN rotation:** add new version to Secret Manager secret; Batch picks up
  `versions/latest` automatically.
- **Leaked Claude OAuth blob:** revoke from Anthropic console, then `claude login` +
  `claude-auth rotate`.

### Deployment

Credential infrastructure has no deploy step — it is declarative:

- `.env` is operator-managed.
- Secret Manager secrets are created on first `claude-auth push` / manually for
  `gh-token`.
- IAM bindings are granted by `claude-auth push` (per-user) and manually for `gh-token`
  (per-project; see credential-setup.md).

### Scaling

All auth paths are O(1) per process except `resolve_gcp_token()`, which is O(1) per
batch boundary with proactive refresh.
No auth is on the per-item hot path.

## Open Questions

- Should `GOOGLE_APPLICATION_CREDENTIALS` tempfiles be cleaned at process exit with an
  `atexit` hook? Currently they persist in `$TMPDIR`.
- Should `auth-check` add a Phase 1.5 that verifies `secretmanager.secretAccessor` on
  the Batch SA for every registered secret?
  Currently dispatch discovers this via a `PermissionDenied` at job start.
- Should the Gemini CLI adapter’s “Vertex AI Express” fallback path be formalized (it
  currently depends on whichever of `GOOGLE_API_KEY` / `GOOGLE_CLOUD_PROJECT` +
  `GOOGLE_GENAI_USE_VERTEXAI` the operator sets)?
- Should operator-authored `~/.pi/agent/models.json` drift from the packaged default
  trigger a warning at dispatch?
  Currently silent.

## References

- [metaproc/docs/arch/arch-metaproc-core.md](arch-metaproc-core.md) — §12 adapters,
  §21.14 Secret Manager integration.
- [metaproc/docs/arch/arch-cloud-execution.md](arch-cloud-execution.md) — §2.8 container
  bootstrap contract, §3.10 Secret Manager integration, §3.12 Vertex AI MaaS
  integration.
- [metaproc/docs/runbooks/credential-setup.runbook.md](../runbooks/credential-setup.runbook.md)
  — operator setup recipes.
- [metaproc/docs/runbooks/cloud-dispatch.runbook.md](../runbooks/cloud-dispatch.runbook.md)
  → *GCP Batch (Personal Plan)*.
- [src/metaproc/config/env_vars.py](../../src/metaproc/config/env_vars.py) — the typed
  env-var registry.
- [src/metaproc/cloud/gcp/gcp_credentials.py](../../src/metaproc/cloud/gcp/gcp_credentials.py)
  — GCP credential resolution and token refresh.
- [src/metaproc/cloud/gcp/batch_backend.py](../../src/metaproc/cloud/gcp/batch_backend.py)
  — `GCP_SECRET_REFS`, `resolve_gcp_secret_ref`, `GCPBatchConfig`.
- [src/metaproc/adapters/claude_code.py](../../src/metaproc/adapters/claude_code.py) —
  `check_auth`, `bootstrap`, Personal-Plan flow.
- [src/metaproc/adapters/pi_cli.py](../../src/metaproc/adapters/pi_cli.py) —
  `check_auth`, provider resolution.
- [src/metaproc/adapters/gemini.py](../../src/metaproc/adapters/gemini.py) — Gemini CLI
  adapter.
- [src/metaproc/commands/auth_check.py](../../src/metaproc/commands/auth_check.py) — the
  multi-phase verification command.
- [src/metaproc/commands/claude_auth.py](../../src/metaproc/commands/claude_auth.py) —
  Keychain ↔ Secret Manager push/show/rotate.
- [src/metaproc/cloud/gcp/worker_dispatch.py](../../src/metaproc/cloud/gcp/worker_dispatch.py),
  [src/metaproc/cloud/gcp/orchestrator_dispatch.py](../../src/metaproc/cloud/gcp/orchestrator_dispatch.py),
  [src/metaproc/cloud/gcp/gcp_run_dispatch.py](../../src/metaproc/cloud/gcp/gcp_run_dispatch.py),
  [src/metaproc/cloud/gcp/container_bootstrap.py](../../src/metaproc/cloud/gcp/container_bootstrap.py).

## §N. Credential pool (plan-2026-04-21-auth-credential-pool.md)

Phase 1/2/2b/2c ships an **opt-in labeled credential pool** alongside the legacy
single-secret `claude-auth` / `codex-auth` path.
Operators can keep using `METAPROC_GCP_SECRET_CLAUDE_CREDS` unchanged; pool features
unlock when they push labels through the new surface.

### §N.1 Surface: `metaproc auth`

```
metaproc auth push    --adapter <a> --label <label> [--backend local|gcp-secret-manager]
metaproc auth list    [--adapter] [--status] [--usage]
metaproc auth usage   --adapter <a> --label <label>
metaproc auth check   --adapter <a> --label <label>
metaproc auth enable  --adapter <a> --label <label>
metaproc auth disable --adapter <a> --label <label>
metaproc auth rotate  --adapter <a> --label <label>
metaproc auth prune   [--adapter] [--status] [--older-than-days N] [--yes]
```

`<adapter>` is `claude-code-cli` (Phase 1) or `codex-cli` (P1.2b — ChatGPT OAuth only;
API-key blobs are rejected and routed to `OPENAI_API_KEY` secret_variables). Labels are
operator-chosen (`laptop`, `home`, `work`, …) and must match `[a-z0-9-]{1,40}`.

### §N.2 Backends

- **`--backend gcp-secret-manager`** (default in cloud): one Secret Manager secret per
  labeled credential, `<adapter-short>-auth-<user>-<label>` (e.g.
  `claude-code-auth-levy-laptop`). State lives in Secret labels; payload lives in
  versions. An `active_version` label pins reads to a specific version id so a failed CAS
  on `update_secret` after `add_secret_version` never leaves readers on a
  stale-label/new-blob mismatch.
- **`--backend local`** (default on laptop): single file at
  `~/.metaproc/credentials.json` (0600, parent 0700). etag is `<mtime_ns>:<size>`. All
  writes via `strif.atomic_output_file`; CAS window guarded by `metaproc.io.mkdir_lock`
  (NFS-safe). Unlike GCP, the local backend keeps the full lease holder string in state
  for debugging.

### §N.3 Per-slot credential isolation

Pool dispatch materializes each in-flight agent attempt’s credential into a private
slot:

```
<RUNS_DIR>/<run_id>/.state/auth/<step>/<item>/a<attempt>/
```

Fan-out uses the mapped item key for `<item>`; a scalar agent uses its step key.
`<run_id>` is the path of the current scope relative to `<RUNS_DIR>`, not the logical
task identity that also contains the process name.
Nested processes therefore bind slots to their child scope, and same-named steps in
sibling scopes cannot collide or write credentials outside the run tree.
Both paths use `PoolDispatchConfig`, `SlotCoordinator`, the adapter’s credential scope
and scrub rules, and the shared completion primitive in `pool_dispatch.py`. They scope
the CLI to that slot through its native configuration environment variable:

- Claude Code → `CLAUDE_CONFIG_DIR=<slot_dir>`, with `<slot_dir>/.credentials.json` mode
  0600\.
- Codex → `CODEX_HOME=<slot_dir>/.codex`, with `auth.json` plus a minimal `config.toml`
  pinning `cli_auth_credentials_store = "file"` and `forced_login_method = "chatgpt"` so
  the slot credential can’t be silently overridden by a stray `OPENAI_API_KEY`.

Scalar acquisition and teardown use the run-owned executor because local or GCP-backed
credential storage may block.
A scalar leaf first receives run and host admission, then acquires its credential, and
only then writes durable attempt state.
It cannot hold a Vehicle B label lock while queued behind the run semaphore.
The scalar path uses the same quota preflight primitive as fan-out, with a projected
size of one. Admission or quota refusal fails the step before durable attempt history
begins.

A pool applies only to steps whose adapter matches the configured pool adapter.
A different adapter uses its ambient authentication, and `run-process` emits an explicit
warning naming the step adapter and pool adapter rather than silently skipping the pool.

Higher-precedence OAuth vars (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`,
`CLAUDE_CODE_APIKEY_HELPER`, `CODEX_CREDS_JSON`) are scrubbed from the subprocess env.
`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are not scrubbed because they are explicit
API-key mode; if either would override the selected pooled OAuth slot, dispatch refuses
the slot instead of silently running on the wrong account.

**Concurrency is decoupled from credential count.** Many concurrent items can share the
same pool label simultaneously — the per-label `lease_holder` mutex was specifically
removed in commit `6db36e75b` (mid-2026-04-27 refactor) so a 2-label pool does not cap
`RunPool.max_concurrency` at 2. Selection is stateless
(`SelectionStrategy(PRIORITY_ORDER, …)`); each item independently picks a label,
materializes a per-attempt slot, and spawns a worker.
Tens of items running on the same label is the normal operating mode for a 15-25-wide
local fan-out. The actual scaling constraint is the per-account 5-hour Pro/Max cap,
observed reactively via the pool’s per-label cooling signals (`auth_outcome` events with
`classification: cooling` + `cooling_until_ts`), not preempted by per-label concurrency
caps.

### §N.4 Fallback policy

`--auth-fallback-policy {none,same-provider,cross-provider,both}` controls the slot
coordinator’s walk when a label’s lease fails on the first attempt:

- `none` — retry is left to the generic classifier; the failing step fails with its
  classified reason. Default.
- `same-provider` — next eligible label on the same adapter, ordered by `last_quota_ts`
  ascending (never-hit-quota sorts earliest).
- `cross-provider` — walk the source adapter’s `compatible_fallback_adapters` in order.
  Claude ↔ Codex compatibility is opt-in per step (semantic, not blob-compatible).
- `both` — same-provider first, then cross-provider.

At most one retry per step across policies (same-provider + cross-provider are not
additive).

### §N.5 Deferred recovery primitives

Metaproc contains an enum, coordinator wait helper, checkpoint format, and resume daemon
from an earlier recovery proposal.
`run-process` and `run-parallel` do not expose that proposal as CLI policy, and no
current scheduler path writes its checkpoints or calls its wait helper.

Current fan-out code performs an internal cooling-aware reschedule; scalar exhaustion
fails immediately.
These paths are intentionally not being unified for GTIA v3 before the
successive smoke cohorts demonstrate a concrete recovery requirement.
Bead `mp-tibt` owns the decision to remove the dormant machinery or introduce the
smallest proved replacement.

### §N.6 Resume daemon

`metaproc resume-daemon --runs-dir <dir> [--poll-interval-s 60] [--once]` is installed,
but current dispatch paths do not produce the checkpoint it consumes.
It is not a live recovery path for new runs.

Long-lived polling loop.
Scans `<runs_dir>/*/*/.state/retry_later.yaml`, re-dispatches
`metaproc run-process --from <step_id>` when `now >= cooling_until_ts`, archives
succeeded checkpoints as `retry_later.resumed.yaml`, archives exhausted
(`retries_attempted >= max_retries`) as `retry_later.exhausted.yaml` with a `.reason`
tombstone.

Deliberately dumb: no classification, no LLM in the loop — per spec goal G15 the thing
that makes overnight runs complete is a 200-line polling daemon, not an agent.

### §N.7 Pre-flight quota gate

`--auth-preflight-quota-guard {off,warn,refuse}` runs before fan-out begins.
Sums each adapter’s eligible-label `query_quota_usage` against
`fan_out × mean_cost_per_item × 1.2`:

- `off` — no check.
- `warn` — logs a `quota_warn` event on near-empty, always returns `go`.
- `refuse` — returns `refuse` when projected > 80% of pool remaining; the caller stops
  before launching work.

Defaults to `warn`; deadline-run playbooks set `refuse`.

### §N.8 Circuit breaker

`StartupFailureCircuit` (in `dispatch/slot_coordinator.py`) trips when N consecutive
workers exit code 1 in under `short_exit_s` without an interleaved success.
The signature catches the 2026-04-23 pattern (rate-limit-rejected CLI exits in 15-45s
with exit=1 vs. real work’s 10-22 min).
Advisory: the `RunPool` owns the subsequent lease swap + mark_cooling decision.

### §N.9 Never-print-payload invariant

Enforced across every surface that touches the pool: blobs never leave this module via
stdout, stderr, logs, or events.
Event schemas (`auth_outcome`, `retry_later`, `quota_warn`) carry fingerprints + labels
only. Pool blob fingerprints are 12-char sha256 prefixes; lease-holder GCP labels are
16-char prefixes. The full lease holder string only exists in run-local state and the
local backend’s JSON file.

### §N.10 API-key extension (future)

Provider API-key pooling (OpenAI, Anthropic, and Gemini keys managed under the same
inventory and rotation surface) is a separate future-phase spec:
`plan-2026-04-24-auth-api-key-pool-extension.md` (pre-extraction spec, absorbed into
[§N.10](#n10-api-key-extension-future)). It is deliberately scoped outside this work so
the OAuth pool can land and stabilize first.
Pi (`pi-cli`) stays on API-key auth unchanged until that spec lands.

### §N.11 Stale-slot trap on Claude Code Personal Plan — observed pattern

The pool stores credentials in `~/.metaproc/credentials.json` (local backend) or in GCP
Secret Manager. Slot materialization writes the blob to `<slot>/.credentials.json` and
points `CLAUDE_CONFIG_DIR=<slot>` at the worker subprocess.

**Observed pattern (not yet proven invariant):** during the Tuesday 2026-04-28 incident
every alt1 / alt2 auth_outcome event showed `flush_fp == bootstrap_fp` and
`rotated: false`. That’s consistent with Claude Code on macOS writing rotated tokens to
the OS Keychain rather than the slot’s `.credentials.json`, despite `CLAUDE_CONFIG_DIR`
([anthropic/claude-code #19456](https://github.com/anthropics/claude-code/issues/19456)).
But the unchanged-fingerprint observation alone doesn’t fully prove that’s the mechanism
— the rotation might not have occurred during those windows for an unrelated reason, or
the behavior may be CLI-version-specific.

What is empirically certain: when this gap manifests, the pool’s stored refresh token
goes stale on the next server-side rotation, and a future probe / dispatch fails with
`oauth_refresh_status=400` (`invalid_grant`). Anthropic’s changelog claims related fixes
for the OAuth refresh race in Claude Code 2.1.81 / 2.1.117 / 2.1.118; the incident-time
local was 2.1.114; latest at writing is 2.1.119. The behavior should be retested
whenever the pinned CLI version changes.

Sections 2.5 and 5.3 of the pre-extraction research note
`research-2026-04-27-claude-code-oauth-multi-account-failover.md` walk through the
empirical reproduction.

**Mitigations in place** (Phase 10 of
plan-2026-04-27-predict-dispatch-tuesday-2026-04-28.md), independent of whether the
underlying CLI bug is fixed in 2.1.119:

- **Pre-flight probe gate** runs once per dispatch before any items launch and marks any
  label whose stored refresh token is stale as `expired` in the pool, removing it from
  the strategy. By the time workers fan out, every selectable label has a
  freshly-validated credential.
- **Refresh-race retry-via-failover** classifies any race-loser failures as
  `RETRY_AFTER_WAIT` (not `ABORT`). The runpool re-queues the item; the next selection
  picks the alt label.

**Operator workflow:** after `claude /login` interactively, always run
`metaproc auth push --label <X> --probe`. The `--probe` flag immediately runs a real-API
roundtrip so pool state matches reality.
Without the probe, the pool may believe the label is `active` while a real dispatch
would fail (the structural-only `auth check` cannot distinguish a working credential
from one whose stored refresh token is already invalidated server-side).

**Long-term option ranking** (revised after secondary review): prefer
Anthropic-supported paths over reverse-engineered ones —

1. `CLAUDE_CODE_OAUTH_TOKEN` / `claude setup-token` — Anthropic-supported long-lived
   OAuth token (~1 year).
   Sidesteps the refresh race entirely.
   Test whether it covers the supported adapter use cases.
2. `apiKeyHelper` — Anthropic-supported credential-extension hook.
   Output sent as both `X-Api-Key` and `Authorization: Bearer`; unclear whether OAuth
   subscription tokens work via this path or only API keys.
3. `ANTHROPIC_API_KEY` direct — sanctioned high-throughput path; per-token billing
   tradeoff vs Pro/Max flat-fee.
4. Bedrock / Vertex MaaS — sanctioned, multi-cloud, also per-token billing.
5. **Direct OAuth refresh** — reverse-engineered, unsupported for Pro/Max subscription
   OAuth (per opencode-claude-auth’s own disclaimers).
   ToS / breakage risk.
   Last resort.

### §N.12 Adapter diagnostic-log preservation

Slot directories are credential-bearing and must be wiped after every run.
But the adapter writes diagnostic files into the slot during execution
(`claude -d api --debug-file=<slot>/claude-code-debug.log`), and those files are the
only evidence of OAuth-refresh attempts, per-attempt API errors, and `AxiosError`
payloads. Without them, post-mortem on a wave of identical failures is impossible — the
operator only sees `auth_outcome: classification=unknown, reason=exit-code-1`.

**Pattern.** The adapter declares which filenames in the slot are diagnostic (vs.
credential-bearing) via an explicit Protocol method:

```python
class AuthCapableCliAdapter(Adapter, Protocol):
    def diagnostic_filenames(self) -> tuple[str, ...]:
        """Slot-relative filenames the adapter writes for diagnostics."""
        return ()
```

`ClaudeCodeCliAdapter` returns `("claude-code-debug.log",)`. `CodexCliAdapter` and
others currently return `()` until they grow equivalents to `claude -d api`. Filenames
are adapter-namespaced so multiple adapters’ diagnostics coexist in the run’s `.logs/`
tree without collision.

The orchestrator (`run_parallel._teardown_pool_slot`) calls
`SlotCoordinator.preserve_diagnostics(lease, session_log_path)` on **every** teardown
(success and failure) before the slot is wiped.
`preserve_diagnostics` resolves the lease’s adapter, asks for `diagnostic_filenames()`,
and copies each file from the slot into `<step>/.logs/<session-stem>.<filename>` so
diagnostics sit next to the captured stream-json session log.
Anything not in `diagnostic_filenames()` is treated as credential-bearing and stays in
the slot to be `rmtree`d.

**Why not archive into a sibling tree.** A parallel `.auth-archive/` location would
fragment the operator’s mental model — two places to look to correlate a run’s
diagnostics with its session log.
All of a step’s logs live under that step’s `.logs/`.

**Why preserve on success too.** Successful runs may have refreshed the OAuth token, hit
transient 5xx, or surfaced rate-limit boundaries — all useful for post-hoc throughput
analysis. The `.logs/` directory is the operator’s authoritative record of every
dispatch, success or failure.

**Failure-path classifier ordering.** `_classify_and_maybe_retry` runs *before*
`try_compact_log` on every failure path.
`try_compact_log` uses [`strif.atomic_output_file`](https://pypi.org/project/strif/)
with `backup_suffix`, which has a documented brief window where the original log path is
absent (rename to `.bak` → rename of `.partial` into place).
Pre-fix, the classifier could land in that window, see `session_log.exists()==False`,
and fall through to the bug-regex on stderr `exit code 1` even when the saved file
(post-rename-completion) clearly contains `api_error_status: 401`. Reordering eliminates
the race; compaction still runs, just after the classifier has read the complete file.

### §N.13 Failover semantics: `RETRY_AFTER_WAIT` for both refresh-race and api-401

Both classifications recover via failover to the alt label rather than retry on the same
label.
The orchestrator’s retry path adds the failed label to `pool_exclude`, so the next
`acquire_slot` returns the alt; same-label retry is never attempted by construction.
Two implications:

- **`api_status in (401, 403)` is `RETRY_AFTER_WAIT`, not `ABORT`.** The earlier `ABORT`
  for api-401 was over-conservative under the (incorrect) assumption that retry meant
  same-label retry. In the 2026-04-27 multi-label incident, that mistake caused 52/52
  items to permanently fail with `retry_count=0` even though `alt2` was eligible the
  whole time. With `RETRY_AFTER_WAIT`, the cohort recovers within a single dispatch.
- **`mark_expired(label)` from a sibling teardown is a separate guard.** It flips the
  label ineligible for new acquisitions, independent of the retry path.
  The combination — `pool_exclude` for in-flight retries + `mark_expired` for new
  acquisitions — means once the failure is correctly classified, no future item on the
  bad label is possible without operator action.

The `LabelCircuit + canary-confirm migration` design is a future optimization that would
cap wasted-compute on the failing label at ~3 items rather than cohort_size, by tripping
a pool-level circuit and pausing new acquisitions before the entire cohort burns through
the bad label. Deferred P2 because the failover semantics above already guarantee
single-dispatch recovery.

### §N.14 Vehicle A pool redesign (2026-04-28)

(Added 2026-04-28 to track the design that landed across Phases 1-4, 6, 7, 10 of
`plan-2026-04-28-claude-code-auth-vehicle-a-pool-redesign.md`, a pre-extraction spec.)

The 2026-04-27 senior engineering review surfaced two corrections that changed the right
primary architecture:

1. **Current Anthropic precedence is reversed from §N.1-§N.10’s framing.**
   `CLAUDE_CODE_OAUTH_TOKEN` *wins* over stored `/login` credentials, not the other way
   around. Research §F4 carries the corrected chain:
   `cloud-provider → ANTHROPIC_AUTH_TOKEN → ANTHROPIC_API_KEY → apiKeyHelper → CLAUDE_CODE_OAUTH_TOKEN → stored /login credentials`.
2. **`CLAUDE_CODE_OAUTH_TOKEN` is the right primary pool credential.** It is a *static
   bearer credential* (no refresh writeback path), Anthropic- documented for CI use,
   supported on Pro/Max/Team/Enterprise, and eliminates every snapshot-staleness failure
   mode by construction.
   The 2026-04-27 production+production dispatch’s 52/52 burn was the snapshot-pool
   architecture failing exactly as predicted by the failure-mode list in research §F12.

#### Two vehicles, one pool

Each pool entry now carries a **`Vehicle`** field (`OAUTH_TOKEN` or `LOGIN_CREDENTIALS`)
on its `EntryState`. Slot bootstrap branches on `vehicle`:

- **Vehicle A (`OAUTH_TOKEN`, recommended primary)** — the bearer token is the
  credential. Slot dir is created empty (mode `0700`) and never receives a
  `.credentials.json`. `credential_scope_env` injects the token via
  `CLAUDE_CODE_OAUTH_TOKEN=<blob>` plus the hardening flags
  `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` and `DISABLE_UPDATES=1`. `credential_scrub_env`
  omits `CLAUDE_CODE_OAUTH_TOKEN` (it IS the credential) but scrubs every other
  higher-precedence var (`ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, cloud-provider mode
  flags, the provisioning-only `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` /
  `CLAUDE_CODE_OAUTH_SCOPES`). Defense-in-depth: materialize_credential asserts no stray
  `.credentials.json` or `settings.json` exists in the slot — a leaked `apiKeyHelper`
  would otherwise silently outvote the static token in the precedence chain.
- **Vehicle B (`LOGIN_CREDENTIALS`, legacy / fallback)** — the existing
  `.credentials.json` snapshot path described throughout §N.1-§N.13. Preserved for
  accounts where Vehicle A isn’t usable on the pinned CLI version, but new deployments
  should be Vehicle A.

#### Operator-facing surface

The fast path is `metaproc auth setup <label>` — one-step Vehicle A onboarding that
spawns `claude setup-token` interactively, captures the minted token, pushes to both
backends (local + GCP Secret Manager when `METAPROC_GCP_PROJECT` is set), and probes the
live API:

```
metaproc auth setup alt1                          # interactive (default)
claude setup-token | metaproc auth setup alt1     # piped
metaproc auth setup alt1 --token-file <path>      # file (rare)
metaproc auth setup alt1 --backend local          # single-backend
metaproc auth setup alt1 --no-probe               # skip live API probe
```

Token-handling discipline: held in-memory only, never written to disk by `auth setup`,
bridged to inner `auth push` calls via the bearer-token env var
(`CLAUDE_CODE_OAUTH_TOKEN` for claude-code-cli) scoped to the duration of each push, and
the operator’s pre-existing value is restored on exit.

The low-level surface is still available — `auth push` plus optional quota-group
annotations — for unusual flows (scripted onboarding, recovering after partial failure):

```
metaproc auth push --adapter claude-code-cli --label alt1 \
  --vehicle oauth-token --token-file <path> --probe \
  [--account-id <16-hex>] [--organization-uuid <uuid>] \
  [--quota-group {auto|org:UUID|account:HEX|unknown}]
```

For `claude-code-cli`, `--vehicle` defaults to `oauth-token` (Vehicle A is the path
metaproc deploys); operators opt into Vehicle B explicitly with
`--vehicle login-credentials`. Non-Claude adapters default to `login-credentials` (their
only meaningful vehicle today).
Token sourcing precedence on Vehicle A: `--token-file` → stdin →
`$CLAUDE_CODE_OAUTH_TOKEN` env var.

##### Adapter-capability-driven `auth setup`

`auth setup` is gated by an adapter capability rather than a string check on
`adapter == "claude-code-cli"`. The `AuthCapableCliAdapter` Protocol declares a default
`setup_token_command(self) -> list[str] | None` returning `None`. Adapters that support
the interactive mint flow override — `ClaudeCodeCliAdapter` returns
`["claude", "setup-token"]`. Codex/gemini inherit the `None` default (codex declares it
explicitly with a comment about the future-phase token-mint surface).

`setup_cmd` calls `adapter_impl.setup_token_command()`; when it returns `None` it
refuses with an error listing adapters that *do* support the flow (built by walking
`ADAPTER_REGISTRY` for non-`None` returns) and points the operator at
`auth push --vehicle login-credentials` for adapters without a mint command.
The extension point for codex when it grows a setup-token equivalent is a single
override on `CodexCliAdapter` — no `auth setup` change required.

`auth status` text output tags Vehicle A entries `[A]` and Vehicle B `[B]` so a mixed
pool is scannable. `auth status --json` renders `vehicle`, `account_id`,
`organization_uuid`, `quota_group_kind`, `quota_group_value` per-label.

#### Quota-group walk on 429

The classifier’s 429 path now exercises a **quota-group walk**: when a label fails with
`status=cooling`, `_teardown_pool_slot` looks up the failing label’s `quota_group`
(sourced from `organizationUuid` / `account_id` annotations) and adds every sibling
label sharing that group to the per-attempt `pool_exclude`. Anthropic rate-limits at
account level (and per `organizationUuid` per `claude-code#41886`), so walking past the
whole group prevents N×429 retries when alt1 and alt2 share the same org.

Conservative on unknown groups: when `quota_group.kind == "unknown"`, the helper
preserves the existing single-label exclude (operators resolve by annotating push-time).

#### Cloud parity

The Phase 6 work closed the gap: cloud workers now construct the same
`PoolDispatchConfig` as local dispatch.
The chain is
`run-process --cloud --auth-* → OrchestratorDispatchConfig → orchestrator entrypoint → inner run-process --backend gcp-worker --auth-* → worker_dispatch propagates METAPROC_AUTH_* → worker entrypoint → inner run-parallel --backend local --auth-*`.
Each layer calls `AuthPoolFlags.from_env()` / `to_cli_flags()` / `to_env_vars()` so the
authentication env-var names and encodings are sourced from a single typed dataclass.
Both worker and orchestrator dispatch carry that dataclass directly.

#### AuthPoolFlags — single source of truth (Phase 10)

`src/metaproc/dispatch/auth_pool_flags.py` defines the `AuthPoolFlags` frozen dataclass
for the account, backend, fallback and selection policies, label filters, and
cross-quota-group posture.
Its `ClassVar` env-var names come from `MetaprocEnv.<member>.name`. Every dispatch-layer
site that previously hardcoded `"METAPROC_AUTH_*"` strings goes through this dataclass:

```
AuthPoolFlags(...).to_env_vars()   # for Batch job env_vars dicts
AuthPoolFlags.from_env().to_cli_flags()   # for inner cmd builders
```

The same pattern applies to the secret-reference, worker-dispatch,
orchestrator-dispatch, and repository synchronization payloads.

#### Long-term option ranking (revised 2026-04-28)

Per research §F7 + senior review, the recommended option ranking is:

1. **`CLAUDE_CODE_OAUTH_TOKEN` (Vehicle A) — primary.** Static bearer, no refresh
   writeback, eliminates snapshot-staleness by construction.
2. **Stored `/login` credentials in Vehicle B safe mode** — fallback only.
   Per-label durable state with CAS writeback (Phase 5 design).
3. **`apiKeyHelper`** — Anthropic-supported credential-extension hook.
   Whether subscription OAuth tokens work via this path is the an open investigation.
4. **`ANTHROPIC_API_KEY`** — sanctioned high-throughput path; per-token billing
   tradeoff.
5. **Bedrock / Vertex MaaS** — sanctioned, multi-cloud, also per-token.
6. **Direct OAuth refresh** — **deprecated** per research §F7 + senior review.
   Reverse-engineered, not Anthropic- supported for Pro/Max OAuth.
   Acceptable for diagnostic spikes only.

### §N.15 Phase 11 — V-A end-to-end + V-B safe mode (ongoing-backup hardening)

Phase 11 (operator decision 2026-04-28) closed the remaining V-A end-to-end gaps and
hardened V-B as the ongoing backup path.
There is no scheduled V-B deprecation review; both vehicles stay first-class targets
indefinitely. The primacy of Vehicle A is asserted at the recommendation layer (default
`--vehicle` for `claude-code-cli`, operator runbooks lead with `metaproc auth setup`)
not at the capability layer.

#### `pre_fan_out_probe` is vehicle-aware

`probe_credential` and `pre_fan_out_probe` in
[`src/metaproc/dispatch/pool_dispatch.py`](../../src/metaproc/dispatch/pool_dispatch.py)
threaded the default vehicle (Vehicle B) into materialize / scope / scrub, so a Vehicle
A label probed at fan-out time got V-B materialization (writes `.credentials.json`,
omits the bearer token from scope env) and the probe always failed.
Phase 11.1 adds a `vehicle` kwarg to `probe_credential` defaulting to
`LOGIN_CREDENTIALS` for back-compat; `pre_fan_out_probe` reads each entry’s
`state.vehicle` and threads it through.
Mirrors the operator-facing `auth probe` callsite in
[`src/metaproc/commands/auth.py`](../../src/metaproc/commands/auth.py).

#### V-A two-label integration smoke test

[`tests/integration/test_two_label_smoke_vehicle_a.py`](../../tests/integration/test_two_label_smoke_vehicle_a.py)
covers the Vehicle A end-to-end at the slot coordinator level: `scope_env` injects
`CLAUDE_CODE_OAUTH_TOKEN` plus the hardening flags
(`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, `DISABLE_UPDATES=1`); no `.credentials.json` is
written into the slot; `scrub_env` strips `ANTHROPIC_AUTH_TOKEN` /
`CLAUDE_CODE_APIKEY_HELPER` but preserves the bearer-token env var; the failover walk
between two V-A labels picks the alternate label.
Sibling of `test_two_label_smoke.py` (which covers the Vehicle B path).

#### `--auth-cross-quota-group` flag

The classifier’s 429 quota-group expansion (Phase 4) fired unconditionally — more
aggressive than the spec calls for.
Phase 11.3 adds an opt-out flag:

```
metaproc run-process ... --no-auth-cross-quota-group
```

When false, `_teardown_pool_slot`’s cooling branch reverts to the single-label exclude
(no expansion to siblings sharing `quota_group`). Useful for diagnostic dispatches
against a single account where the expansion would empty the pool.
Default true preserves the spec-correct behavior.

The flag travels through the dispatch chain via
[`src/metaproc/dispatch/auth_pool_flags.py`](../../src/metaproc/dispatch/auth_pool_flags.py)
(`auth_cross_quota_group` field, `METAPROC_AUTH_CROSS_QUOTA_GROUP` env var).
The encoder writes the env var/CLI flag only when the operator opts out — default-true
dispatches stay free of the new var.

#### Vehicle B safe mode — per-label lock

Phase 5 adds a per-label mkdir-based lock acquired in `SlotCoordinator.acquire_slot` for
V-B leases and released in `teardown`. Two parallel V-B attempts on the same label
serialize through the lock so the refresh-window race that produces snapshot-staleness
is eliminated.

- New `SlotLease.label_lock_path` field (`None` for V-A; populated for V-B).
- `vehicle_b_lock_dir()` resolves to `~/.metaproc/auth-pool/locks` by default; override
  via `METAPROC_AUTH_POOL_LOCK_DIR` for cross-host coordination on a shared filestore
  mount.
- `METAPROC_AUTH_POOL_LOCK_TIMEOUT_S` overrides the wait timeout (default 300s).
- Stale-after auto-reclaim (10 min) keeps a crashed dispatch from permanently blocking
  the label.
- Materialize-failure release: if `materialize_credential` raises mid-acquire, the lock
  is released so the label isn’t stuck on a half-bootstrapped lease.
- V-A leases bypass the lock entirely (no refresh writeback to protect).

Note: durable per-label storage (the bead’s secondary deliverable) is not implemented in
this pass — the lock alone closes the snapshot-staleness race for two parallel attempts
on one host. Full durable-state design is deferred until ongoing-backup data shows it’s
needed (V-A is the recommended primary).

### §N.16 Phase 10 follow-ups — typed payload cohorts

Phase 10 introduced [`AuthPoolFlags`](../../src/metaproc/dispatch/auth_pool_flags.py) —
a frozen dataclass that wraps the `METAPROC_AUTH_*` env-var cohort with `from_env` /
`to_env_vars` / `to_cli_flags` and `ClassVar` env-var names sourced from `MetaprocEnv`.
The pattern eliminates ~25 hardcoded `"METAPROC_AUTH_*"` string literals across the
dispatch chain and made the implementation worker-leg gap impossible (any rename now
fails at import-time, not silently at dispatch).

Phase 11 closed the four follow-up beads applying the same pattern to other env-var
cohorts that travel together.

| Module | Cohort |
| --- | --- |
| [`metaproc/dispatch/secret_refs.py`](../../src/metaproc/dispatch/secret_refs.py) | `SecretRef` and `SecretRefSet` for the `GCP_SECRET_REFS` cohort: plaintext environment variable, Secret Manager reference variable, and human description. `SecretRefSet.all_known()` composes static refs (`GH_TOKEN`, `CLAUDE_CODE_CREDS_JSON`, `CODEX_CREDS_JSON`) with provider-derived refs from `gcp_secret_refs()`. `to_secret_variables()` produces the Batch API’s `secret_variables` mapping. `as_tuples()` preserves the legacy 3-tuple shape for backward compatibility. |
| [`metaproc/dispatch/repo_sync_payload.py`](../../src/metaproc/dispatch/repo_sync_payload.py) | `RepoSyncPayload` for the four-field repo-sync cohort: `METAPROC_REPO_URL`, `METAPROC_RUN_BRANCH`, `METAPROC_WHEEL_GCS`, `METAPROC_WORKSPACE_GCS`. Used by `container_bootstrap`, `orchestrator_dispatch`, `worker_dispatch`. |
| [`metaproc/dispatch/orchestrator_payload.py`](../../src/metaproc/dispatch/orchestrator_payload.py) | `OrchestratorDispatchPayload` for the 13-field operator-CLI-to-orchestrator cohort. Replaces about 50 lines of conditional `env_vars` manipulation with one constructor and `update`. Spot defaults to true (silent emission); `num_workers` always emits both `METAPROC_NUM_WORKERS` and `METAPROC_DEFAULT_NUM_WORKERS` to tolerate code-version drift. |
| [`metaproc/dispatch/worker_payload.py`](../../src/metaproc/dispatch/worker_payload.py) | `WorkerDispatchPayload` for the 12-field orchestrator-to-worker cohort. Worker identity is load-bearing and always emitted; inline and file item contexts are mutually exclusive, with the file path winning when both are populated. Call-site migration in `worker_dispatch` is deferred because the existing inline construction handles size-gated spill and NFS path resolution; the dataclass is ready for new sites. |

The pattern is now well-trodden: any new env-var cohort that travels together gets a
typed module mirroring this shape.
The `setup_token_command` capability seam (Phase 7.3) follows the same principle at the
Protocol level.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
