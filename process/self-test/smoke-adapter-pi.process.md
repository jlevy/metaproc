---
process:
  name: smoke-adapter-pi
  description: >-
    Per-adapter smoke for pi-cli. Confirms the binary is on PATH, the
    `auth.json` credential source is detectable, and a trivial prompt
    round-trips through Vertex MaaS (free GLM-5 MaaS credits — no paid
    tokens consumed). Uses vertex-maas because it is the cheapest live
    path this repo exercises; other providers (anthropic, openai,
    google-vertex) would work with corresponding credentials but incur
    real cost.

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
        bash -lc "cd ../../.. && uv run metaproc auth-check --live --variant pi-cli-glm-5-maas --provider vertex-maas --assert-model glm-5-maas"
      description: >-
        Send a trivial "Respond with exactly: OK" prompt through
        Vertex MaaS GLM-5 and assert the `model` field in the
        `agent_start` event contains "glm-5-maas" (substring handles
        provider-prefixed IDs like `zai-org/glm-5-maas`). Pre-flight
        verifies pi registration before dispatch; the model assertion
        closes the loop after the prompt returns.
      needs: [auth-check-dry]
---
# smoke-adapter-pi — live smoke for pi-cli

Three-step gate for pi-cli: binary → credential → live prompt on Vertex MaaS.

## Steps

1. **binary-check** — `pi --version`.
2. **auth-check-dry** — `metaproc auth-check --variant pi-cli`.
3. **live-probe-vertex-maas** — live probe against `glm-5-maas` on `vertex-maas`, with
   `--assert-model glm-5-maas` to verify the observed `agent_start` event’s `model`
   field (substring match so provider-prefixed IDs like `zai-org/glm-5-maas` still
   pass). Requires GCP Application Default Credentials
   (`gcloud auth application-default login`) or `GCP_CREDENTIALS_BASE64` /
   `GOOGLE_APPLICATION_CREDENTIALS`.

The pi adapter supports multiple providers — `anthropic`, `openai`, `google-vertex`,
`vertex-maas`. This smoke pins `vertex-maas` because GLM-5 MaaS credits are free in this
project. To probe a paid provider, override the variant:

```bash
uv run metaproc auth-check --live --variant pi-cli-opus --provider anthropic
```

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapter-pi.process.md
```
