---
process:
  name: smoke-adapter-pi
  description: >-
    Per-adapter smoke for pi-cli. Confirms the binary is on PATH, the
    `auth.json` credential source is detectable, and a trivial prompt
    round-trips through Vertex MaaS GLM-5.2 (a fraction of a cent per
    run). Uses vertex-maas because it exercises the packaged custom
    provider that no other smoke covers; other providers (anthropic,
    openai, google-vertex) work with corresponding credentials.

  steps:
    - id: binary-check
      mode: code
      command: >-
        bash -lc "pi --version"
      description: Confirm the pi-cli binary is installed and on PATH.

    - id: auth-check-dry
      mode: code
      command: >-
        bash -lc "cd ../../.. && uv run metaproc auth-check --variant pi-cli"
      description: Dry survey — binary path + credential source (`~/.pi/auth.json`).
      needs: [binary-check]

    - id: live-probe-vertex-maas
      mode: code
      command: >-
        bash -lc "cd ../../.. && uv run metaproc auth-check --live --adapter pi-cli --model glm-5.2-maas --provider vertex-maas --assert-model glm-5.2-maas"
      description: >-
        Send a trivial "Respond with exactly: OK" prompt through
        Vertex MaaS GLM-5.2 and assert the `model` field in the
        `agent_start` event contains "glm-5.2-maas" (substring handles
        provider-prefixed IDs like `zai-org/glm-5.2-maas`). Pre-flight
        verifies pi registration before dispatch; the model assertion
        closes the loop after the prompt returns.
      needs: [auth-check-dry]
---
# smoke-adapter-pi — live smoke for pi-cli

Three-step gate for pi-cli: binary → credential → live prompt on Vertex MaaS.

## Steps

1. **binary-check** — `pi --version`.
2. **auth-check-dry** — `metaproc auth-check --variant pi-cli`.
3. **live-probe-vertex-maas** — live probe against `glm-5.2-maas` on `vertex-maas`, with
   `--assert-model glm-5.2-maas` to verify the `message.model` field of the first
   assistant `message_start` event (substring match so provider-prefixed IDs like
   `zai-org/glm-5.2-maas` still pass).
   Google retired GLM-5 on Vertex MaaS on 2026-10-21; GLM-5.2 is a preview model with a
   45-day minimum availability, so recheck the ID when this smoke starts failing.
   Requires GCP Application Default Credentials
   (`gcloud auth application-default login`) or `GCP_CREDENTIALS_BASE64` /
   `GOOGLE_APPLICATION_CREDENTIALS`.

The pi adapter supports multiple providers — `anthropic`, `openai`, `google-vertex`,
`vertex-maas`. This smoke pins `vertex-maas` because no other smoke exercises the
packaged custom provider.
To probe another provider, name it and a model:

```bash
uv run metaproc auth-check --live --adapter pi-cli --model opus --provider anthropic
```

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapter-pi.process.md
```
