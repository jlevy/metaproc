---
title: Adding a New LLM Provider
description: Step-by-step checklist for plumbing a new pi-cli provider end-to-end (registry → catalog → pricing → secrets → smoke). Single-source-of-truth via metaproc/config/providers.py.
---
# Adding a New LLM Provider

Checklist for adding a new LLM provider that metaproc dispatches via pi-cli (e.g.
DeepSeek V4, Moonshot Kimi K2.6, the next first-party API to land).
The design intent is that the **provider registry** in
[`metaproc/config/providers.py`](../../src/metaproc/config/providers.py) is the single
source of truth: edit one entry there and the rest of the codebase picks it up
automatically or the parity tests fail loudly.

## When to use this runbook

- You have a provider with a first-party API (OpenAI-compatible or similar) and want
  pi-cli to dispatch directly to it (not via Vertex MaaS).
- You have a new Vertex MaaS publisher slot and want pi-cli to route through it.

If you only need to add a *model* to an existing provider, edit `pi-models.default.json`
and `pricing.md` — no registry change needed.

## The contract

A new provider touches these surfaces.
The registry collapses most of them into a single edit; the rest are data files and
tests.

| Surface | File | Source of truth |
| --- | --- | --- |
| Provider name + label + env var + GCP secret + name-prediction | [providers.py](../../src/metaproc/config/providers.py) `PROVIDERS` | **Edit here first** |
| Env var declaration | [env_vars.py](../../src/metaproc/config/env_vars.py) `MetaprocEnv` | Add `<NAME>_API_KEY` and `METAPROC_GCP_SECRET_<NAME>_API_KEY` |
| `.env.example` | repo root `.env.example` | Regenerate: `metaproc env --template > .env.example` |
| pi-cli model catalog | [pi-models.default.json](../../src/metaproc/data/pi-models.default.json) | Add provider block + at least one model |
| Pricing rows | [pricing.md](../../src/metaproc/data/pricing.md) | Add provider block + comparison-table rows |
| Cloud dispatch | [secret_refs.py](../../src/metaproc/dispatch/secret_refs.py) `SecretRefSet` | Auto-derived from provider registry |
| pi-cli auth detection | [pi_cli.py](../../src/metaproc/adapters/pi_cli.py) `check_auth` / `auth_info` | Auto-derived from registry |
| `auth-check --live` provider inference | [auth_check.py](../../src/metaproc/commands/auth_check.py) `_infer_pi_provider` | Auto-derived from registry |
| `PI_VALID_PROVIDERS` | [settings.py](../../src/metaproc/settings.py) | Auto-derived from registry |
| Operator-side pi catalog | `~/.pi/agent/models.json` | Mirror the new block from `pi-models.default.json` (operator setup, not committed) |

## Step-by-step

### 1. Add the registry entry

Edit `metaproc/config/providers.py` and append a `ProviderSpec` to `PROVIDERS`. Place
direct-API providers AFTER the `vertex-maas` entry so any `*-maas` model still infers
Vertex MaaS.

```python
ProviderSpec(
    name="deepseek",                                # pi-cli provider name
    label="DeepSeek",                               # human-readable
    api_key_env=MetaprocEnv.DEEPSEEK_API_KEY,
    gcp_secret_ref_env=MetaprocEnv.METAPROC_GCP_SECRET_DEEPSEEK_API_KEY,
    gcp_secret_description="DeepSeek API key (pi-cli deepseek provider, V4 direct API)",
    model_name_match=lambda n: n.startswith("deepseek-v"),
),
```

`model_name_match` decides which provider `auth-check --live --variant pi-cli-<model>`
infers when the operator does not pass an explicit `--provider`. Without a working
predicate, the live check false-greens on a fallback provider.
Predicate must be stricter than the `vertex-maas` `*-maas` rule but match every
legitimate model ID.

### 2. Declare the env vars

Edit `metaproc/config/env_vars.py`:

```python
DEEPSEEK_API_KEY = secret(
    "DeepSeek API key for the pi-cli `deepseek` provider (DeepSeek V4 direct API)."
)
METAPROC_GCP_SECRET_DEEPSEEK_API_KEY = optional(
    "GCP Secret Manager ref that provides DEEPSEEK_API_KEY to Batch workers ..."
)
```

Add `MetaprocEnv.DEEPSEEK_API_KEY` to the `SECRET_VARS` frozenset so the value is masked
in operator-facing output.

### 3. Regenerate `.env.example`

```sh
uv --config-file uv.toml run --frozen metaproc env --template > .env.example
```

`tests/test_env_template.py` is the gate that fails if you skip this.

### 4. Add the pi-cli catalog block

Edit `src/metaproc/data/pi-models.default.json`. Mirror the `openai` block for
OpenAI-compatible providers:

```json
"deepseek": {
  "_note": "DeepSeek first-party API (V4 family). OpenAI-compatible /v1/chat/completions.",
  "baseUrl": "https://api.deepseek.com/v1",
  "api": "openai-completions",
  "apiKey": "DEEPSEEK_API_KEY",
  "models": [
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "reasoning": true,
     "input": ["text"], "contextWindow": 1000000, "maxTokens": 384000}
  ]
}
```

> **`apiKey` syntax** (per pi-mono
> [custom-provider docs](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/custom-provider.md)):
> use the **bare env var name** (e.g. `"DEEPSEEK_API_KEY"`). The `!` prefix is for shell
> commands (e.g. `"!gcloud auth print-access-token"`); a value like
> `"!env:DEEPSEEK_API_KEY"` is interpreted as a shell command and fails with
> `Failed to resolve API key … from shell command: env:…`.

`tests/test_pi_valid_models_catalog.py` is the gate that fails if any model ID here is
not also in `PI_VALID_MODELS` (settings.py).

### 5. Add pricing rows

Edit `src/metaproc/data/pricing.md`:

- Add a top-level provider block under `providers:` in the YAML frontmatter, with one
  entry per model.
- Add matching rows in the markdown comparison table (sorted by output cost descending).
- Bump `last_updated` at the top of the frontmatter.

`tests/test_usage.py::TestPricing` enforces frontmatter ↔ table parity and
`last_updated >= max(last_reviewed)`.

### 6. Mirror the catalog block to the operator’s pi config

For local `pi --list-models` and direct invocation to recognize the new provider, add
the same block to `~/.pi/agent/models.json`. This is operator setup, not committed to
the repo. Cloud dispatch picks up the canonical block from `pi-models.default.json`
automatically via `build_pi_models_json` in `batch_backend.py`.

```sh
# Backup first
cp ~/.pi/agent/models.json ~/.pi/agent/models.json.bak.$(date +%Y%m%d)
# Then merge in the new provider block (jq, hand-edit, or copy from
# the canonical src/metaproc/data/pi-models.default.json)
```

### 7. Provision real credentials

- `.env`: set the new `*_API_KEY`.
- GCP Secret Manager: create the secret and point the matching
  `METAPROC_GCP_SECRET_*_API_KEY` ref at it for cloud dispatch.

### 8. Smoke

Run the full test suite first, then live-check each variant:

```sh
uv --config-file uv.toml run --frozen pytest --ignore=tests/integration

uv --config-file uv.toml run --frozen metaproc auth-check
uv --config-file uv.toml run --frozen metaproc auth-check \
  --live --variant pi-cli-<new-model> --timeout 30
```

`auth-check --live` for pi-cli now parses pi-cli’s JSONL stream and classifies failures:
`credentials-missing`, `unauthorized`, `forbidden`, `rate-limited`, `quota-exhausted`,
`model-not-found`, `timeout`, `network`, `upstream-5xx`. A clean run with the key set
should report `[+] pi-cli (<model>): live check passed`.

## What goes wrong if you skip a step

| Skipped step | Symptom |
| --- | --- |
| Registry entry | Auth-check live false-greens (provider inference returns None, pi-cli falls back to its default) |
| Env var declaration | `KeyError: <NAME>_API_KEY` at import; tests fail loudly |
| `.env.example` regen | `tests/test_env_template.py::test_template_matches_checked_in_dot_env_example` fails |
| Catalog JSON | `tests/test_pi_valid_models_catalog.py` passes (model IDs match) but pi-cli has no provider block to dispatch through |
| `PI_VALID_MODELS` (only triggered if you bypass the registry) | Adapter silently falls back to `PI_DEFAULT_MODEL=sonnet` and retry-storms |
| Pricing rows | `usage.md` reports show `unknown model` and `actual_cost = 0` for every run on the new model |
| Operator pi config | Local `pi --list-models <name>` does not list the provider; `auth-check --live` errors at `_pi_validate_registration` |

## Why This Layout

Provider metadata is centralized so a new provider is one registry entry plus three data
files (`env_vars.py`, `pi-models.default.json`, `pricing.md`), each with its own parity
gate. Hard-coding provider names at call sites turns every new provider into a
multi-touchpoint diff with no enforcement that all sites were updated; the parity tests
catch some omissions but not all, so the single-registry shape is the guardrail.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
