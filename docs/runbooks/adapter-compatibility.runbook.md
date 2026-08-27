---
runbook:
  title: Adapter Compatibility
  description: pi-cli API-path routing, Gemini 3 thought_signature, ADC on Batch, and the `derive_variant` cascade — durable adapter quirks every dispatch operator needs to know.
  category: metaproc
---
# Adapter Compatibility

Durable reference for the adapter-routing nuances that have caused production retry
storms and silent variant splits.
Read this once, then refer back when adding a model, debugging a 400-storm, or wiring a
new step.

Operational dispatch checklist lives in
[`cloud-dispatch.runbook.md`](../../src/metaproc/docs/cloud-dispatch.runbook.md).

## 1. pi-cli (pi-mono) API-path matrix

`pi-mono` ships a matrix of API-type providers
(`packages/ai/src/providers/register-builtins.ts`). Only some handle Vertex’s Gemini 3
`thought_signature` round-trip:

| API type | Module | Handles Gemini 3 thought_signature? |
| --- | --- | --- |
| `openai-completions` | `openai-completions.ts` | **No** — generic OpenAI wire; only knows `reasoning_details` / `reasoning.encrypted`, not Vertex’s `extra_content.google.thought_signature`. |
| `google-vertex` | `google-vertex.ts` + `google-shared.ts` | **Yes** — uses `@google/genai` SDK directly; explicit Gemini 3 branch in `google-shared.ts:159`: *“Gemini 3 requires thoughtSignature on all function calls when thinking mode is enabled”*. |
| `google-generative-ai` | `google.ts` | Yes (same google-shared path). |
| `google-gemini-cli` | `google-gemini-cli.ts` | Yes. |

**Implication:** any Vertex Gemini variant must route through `api: "google-vertex"` in
`src/metaproc/data/pi-models.default.json`. Routing through `openai-completions` causes
every second-turn tool call to drop `extra_content.google.thought_signature` on the way
out, and Vertex returns 400. This is the failure mode that retry-stormed the Gemini r5
100-item baselines for 100 events each.

## 2. Vertex multi-turn tool probe

The probe that catches the above before a retry storm.

```bash
ACCESS_TOKEN=$(gcloud auth print-access-token)
BASE='https://aiplatform.googleapis.com/v1/projects/exampletool/locations/global/endpoints/openapi'

# Multi-turn tool flow — replay an assistant tool call and append the tool result.
# Pre-fix: 400 "missing thought_signature" on Gemini 3 previews.
curl -sS -X POST "$BASE/chat/completions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"model":"google/gemini-3-flash-preview","messages":[
    {"role":"user","content":"Look up ACME data"},
    {"role":"assistant","content":null,"tool_calls":[
       {"id":"c1","type":"function","function":{"name":"get_data","arguments":"{\"item\":\"ACME\"}"}}]},
    {"role":"tool","tool_call_id":"c1","content":"{\"price\":237.59}"}],
   "max_tokens":80,
   "tools":[{"type":"function","function":{"name":"get_data",
      "parameters":{"type":"object","properties":{"item":{"type":"string"}},"required":["item"]}}}]}'
```

Run the same payload against every entry in `google-vertex` and `vertex-maas` providers
before a 100-item dispatch.
Any 400/5xx cancels dispatch.

This should eventually fold into `metaproc auth-check --live --catalog-matrix`; use the
manual reproducer above until that lands.

## 3. ADC on GCP Batch (no apiKey path)

`google-vertex.ts:372-400` has two code paths:

- **With `apiKey`** — `new GoogleGenAI({vertexai: true, apiKey, apiVersion})`.
- **Without `apiKey`** —
  `new GoogleGenAI({vertexai: true, project, location, apiVersion})`, which picks up
  Application Default Credentials automatically.

On Batch VMs the GCE metadata server already supplies ADC as `user@example.invalid`, so
no explicit API key or key-file is needed.
The container injects `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` at dispatch
time ([`worker_dispatch.py`](../../src/metaproc/cloud/gcp/worker_dispatch.py),
[`orchestrator_dispatch.py`](../../src/metaproc/cloud/gcp/orchestrator_dispatch.py)).

`GOOGLE_CLOUD_LOCATION=global` is the only correct value for the native `google-vertex`
API. `@google/genai` with `location: "global"` resolves to
`https://aiplatform.googleapis.com/`. Other values break Gemini 3 preview routing.

`apiKey: "gcp-vertex-credentials"` sentinel in the provider block passes pi-cli’s
`hasConfiguredAuth` gate while ADC actually drives auth.

## 4. `derive_variant` cascade

Every step whose output path references `{{run.variant}}` must resolve to the same
variant as the fan-out step it shares a directory with.
A per-step `adapter.config.model` override re-derives `VARIANT` and silently splits
files across sibling dirs, causing QA to report `FAIL 15/2` on every record.

**Rule:** any step that overrides `adapter.config.model` (e.g. to use a cheaper or
faster model for finalization) must also pin `variant:` explicitly.

Lock this rule into each downstream package’s execution-profile consistency tests.
The framework test strategy is documented in
[arch-testing](../../src/metaproc/docs/arch-testing.md).

Helper for verifying a single step’s resolution:

```python
from metaproc.adapters.registry import derive_variant
# claude-code-cli + opus       → "claude-cli"
# claude-code-cli + sonnet     → "claude-cli-sonnet"
# pi-cli          + glm-5-maas → "pi-cli-glm-5-maas"
```

## 5. Provider configuration shape

`src/metaproc/data/pi-models.default.json` is the source of truth for which API each
model uses. The `google-vertex` provider block must use the native API path, with naked
model IDs (no `google/` prefix — the publisher path is baked into the `@google/genai`
SDK):

```jsonc
"google-vertex": {
  "baseUrl": "https://{location}-aiplatform.googleapis.com",
  "api": "google-vertex",
  "apiKey": "gcp-vertex-credentials",  // sentinel; ADC drives actual auth
  "models": [
    { "id": "gemini-3.5-flash", ... },
    { "id": "gemini-3.1-flash-lite", ... },
    { "id": "gemini-3-flash-preview", ... },
    { "id": "gemini-3.1-pro-preview", ... },
    { "id": "gemini-3.1-pro-preview-customtools", ... }
  ]
}
```

Allowlist (`src/metaproc/settings.py:PI_VALID_MODELS`) must list the same naked model
IDs. The `test_pi_valid_models_catalog.py` regression test enforces parity.

`vertex-maas` (GLM-5, DeepSeek, Qwen, GLM 4.7, Kimi) stays on `openai-completions` —
those are MaaS-hosted OpenAI-compat models that don’t go through Vertex’s native Gemini
API.

## 6. Tool-use attribution (post-hoc, from emitted data)

The `derive_variant` cascade (§4) and the provider-configuration shape (§5) together
describe which adapter-path a variant **should** route through at dispatch time.
Runbook gap: pre-dispatch inspection is easy, but post-hoc confirmation ("did W1
actually hit Vertex MaaS GLM-5 or did it fall back to the OpenAI-compat surface?") used
to require log archaeology.

`ProviderRateLimitStats` closes that gap.
The pi-cli log parser writes a `rate_limit_event` record whenever the adapter receives a
provider rate-limit response; the metaproc aggregator bins those events by
`(provider, adapter, variant)` and surfaces them in the `usage.md` frontmatter
`rate_limit_stats` block:

```yaml
rate_limit_stats:
  - provider: vertex-maas
    adapter: pi-cli
    variant: pi-cli-glm-5-maas
    count: 42
```

If the provider string on a rate-limit event doesn’t match the provider the variant was
*supposed* to route to, the variant silently routed somewhere else — usually because a
local shell had a stray env var overriding the intended adapter path, or because the
image shipped with a stale `derive_variant` cascade.
Either way, the emitted stats make the mis-routing visible.

Full contract:
[`metaproc-design.md §14.7 Tool-use Observability`](../../src/metaproc/docs/metaproc-design.md).

## 7. Endpoint-shape signals (incidental but useful)

Probed against `gemini-3-flash-preview` on 2026-04-18:

- `response_format: json_object` and `response_format: json_schema` (strict) both work.
- `stream: true` works.
- Native `tools: [{"type": "web_search"}]` is rejected — the OpenAI-compat surface only
  accepts function tools.
  Native grounding runs through `web_search_options` or the native
  `streamGenerateContent` API.
- `role: developer` messages are accepted (contrary to the
  `compat.supportsDeveloperRole: false` hint pi-cli’s MaaS-provider shape carries).

## 8. Gemini smoke matrix (laptop coverage)

Nine-cell matrix proves both harnesses can dispatch every registered Gemini model
end-to-end with tool use intact.
Lives at
[`process/self-test/smoke-gemini-matrix.process.md`](../../process/self-test/smoke-gemini-matrix.process.md).
Independent of the openai-completions blast radius covered above — this is the
regression guard that catches it.

| Harness | gemini-3.5-flash | gemini-3.1-pro-preview | gemini-3.1-flash-lite | gemini-3-flash-preview | gemini-3-pro-preview |
| --- | --- | --- | --- | --- | --- |
| `gemini-cli` | ✓ (12s) | ✓ (30s) | ✓ (9s) | ✓ (11s) | ✓ (15s) |
| `pi-cli` (google-vertex) | ✓ (8s) | ✓ (15s) | ✓ (6s) | ✓ (9s) | — not in pi catalog |

Each cell chains `auth-check --live --adapter <h> --model <m> --assert-model <m>` with
`probe-tool-use --harness <h> --model <m>`. The probe writes a high-entropy sentinel to
a tempfile under `.metaproc-probe/<hash>/` and verifies the model echoed it through a
file-read tool round-trip — this catches a model that text-generates fine but cannot
complete tool calls (e.g. Gemini-3 `thought_signature` regressions).

To run:

```bash
uv run metaproc run-process process/self-test/smoke-gemini-matrix.process.md \
  --var RUNS_DIR=/tmp/metaproc-runs --var RUN_ID=gemini-matrix-$(date +%Y%m%dT%H%M%S)
```

Last verified: 2026-05-25 — all 9 cells PASS (full matrix wall clock: 2m).

## 9. Execution-profile workflow smoke (operator-facing readiness)

Cross-harness “deep” smoke that exercises every shipped execution profile via
`metaproc probe-tool-use --execution-profile <name>`. Proves operator
`--execution-profiles <name>` invocations from large workflow dispatch (or ad-hoc
`metaproc run-process`) actually work end-to-end including tool use.
Lives at
[`process/self-test/smoke-execution-profiles.process.md`](../../process/self-test/smoke-execution-profiles.process.md).

| Profile | Adapter | Model | Status |
| --- | --- | --- | --- |
| `claude-opus` | claude-code-cli | opus | ✓ (8s) |
| `claude-sonnet` | claude-code-cli | sonnet | ✓ (9s) |
| `codex-gpt55` | codex-cli | gpt-5.5 | ✓ (8s) |
| `pi-glm5` | pi-cli | glm-5-maas | ✓ (6s) |
| `gemini-flash` | gemini-cli | gemini-3.5-flash | ✓ (8s) |
| `gemini-pro` | gemini-cli | gemini-3.1-pro-preview | ✓ (9s) |
| `pi-gemini-flash` | pi-cli | gemini-3.5-flash (google-vertex) | ✓ (5s) |

To run:

```bash
uv run metaproc run-process process/self-test/smoke-execution-profiles.process.md \
  --var RUNS_DIR=/tmp/metaproc-runs --var RUN_ID=exec-profiles-$(date +%Y%m%dT%H%M%S)
```

Last verified: 2026-05-25 — all 7 cells PASS (wall clock: 48s).

The probe phrases its prompt as a declarative user question
(`"What is the first line of file X? Read it..."`) rather than an imperative instruction
block, because claude-code-cli’s prompt-injection heuristic flags imperatives passed via
`-p @<file>` as suspicious.
The wording is otherwise harness-agnostic.

## See also

- [`cloud-dispatch.runbook.md`](../../src/metaproc/docs/cloud-dispatch.runbook.md) —
  pre-launch gates, required env, dispatch command shape.
- [`credential-setup.runbook.md`](../../src/metaproc/docs/credential-setup.runbook.md) —
  per-adapter credential wiring.
- [pi-mono source](https://github.com/mariozechner/pi): check out the external
  repository when adapter internals need review via the `checkout-third-party-repo`
  shortcut.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
