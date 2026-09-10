---
last_updated: '2026-07-24'

providers:
  openai:
    models:
      gpt-5.5:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 30.00
          cache_read_per_1m: 0.50
        cost_source: computed
        source_url: 'https://openai.com/api/pricing'
        last_reviewed: '2026-04-27'
        notes: "Reviewed 2026-04-27. Released 2026-04-23, API 2026-04-24. 1M context. Per OpenAI's public announcement: $5/M input, $30/M output. 90% cached-input discount per the GPT-5 family formula. Model availability and lifecycle are maintained in model_catalog.py."
      gpt-5.5-pro:
        actual_price:
          input_per_1m: 30.00
          output_per_1m: 180.00
        cost_source: computed
        source_url: 'https://openai.com/api/pricing'
        last_reviewed: '2026-04-27'
        notes: "GPT-5.5 Pro — extra-compute reasoning variant. Released alongside gpt-5.5 (2026-04-24 API). $30/M input, $180/M output per OpenAI's public announcement. No public cached-input rate listed; if one appears on openai.com/api/pricing, add `cache_read_per_1m`."
      gpt-5.4-mini:
        actual_price:
          input_per_1m: 0.75
          output_per_1m: 4.50
          cache_read_per_1m: 0.075
        cost_source: computed
        source_url: 'https://openai.com/api/pricing'
        last_reviewed: '2026-04-27'
        notes: "Reviewed 2026-04-27. 90% cached-input discount per the GPT-5 family formula. Cross-verified via OpenRouter 2026-04-14. Model availability and lifecycle are maintained in model_catalog.py."
      gpt-5.4-nano:
        actual_price:
          input_per_1m: 0.20
          output_per_1m: 1.25
          cache_read_per_1m: 0.02
        cost_source: computed
        source_url: 'https://openai.com/api/pricing'
        last_reviewed: '2026-04-27'
        notes: "Reviewed 2026-04-27 and cross-verified via OpenRouter 2026-04-14. Model availability and lifecycle are maintained in model_catalog.py."

  anthropic:
    models:
      claude-opus-4-7:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-04-24'
        notes: "Rates carried over from 4.6, not independently verified for this model. Cross-verify against platform.claude.com/docs/en/about-claude/pricing before production cost rollups. cache_write_per_1m uses the 5-minute prompt-cache write rate. Model availability and lifecycle are maintained in model_catalog.py."
      claude-opus-4-6:
        actual_price:
          input_per_1m: 5.00
          output_per_1m: 25.00
          cache_read_per_1m: 0.50
          cache_write_per_1m: 6.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-04-02'
        notes: "Anthropic direct API pricing. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-sonnet-4-6:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.30
          cache_write_per_1m: 3.75
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-04-02'
        notes: "Anthropic direct API pricing. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-sonnet-4-5:
        actual_price:
          input_per_1m: 3.00
          output_per_1m: 15.00
          cache_read_per_1m: 0.30
          cache_write_per_1m: 3.75
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-04-02'
        notes: "Anthropic direct API pricing. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."
      claude-haiku-4-5:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 5.00
          cache_read_per_1m: 0.10
          cache_write_per_1m: 1.25
        cost_source: self-reported
        source_url: 'https://platform.claude.com/docs/en/about-claude/pricing'
        last_reviewed: '2026-04-02'
        notes: "Anthropic direct API pricing. cache_write_per_1m uses Anthropic's 5-minute prompt-cache write rate."

  google:
    models:
      gemini-3.6-flash:
        actual_price:
          input_per_1m: 1.50
          output_per_1m: 7.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-07-24'
        notes: "Reviewed 2026-07-24. Gemini 3.6 Flash was GA 2026-07-21. Google listed the same input price as Gemini 3.5 Flash with a lower output price, 1M input context, 64K maximum output, and medium default thinking. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3.5-flash:
        actual_price:
          input_per_1m: 1.50
          output_per_1m: 9.00
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-05-23'
        notes: "Reviewed 2026-05-23. Gemini 3.5 Flash was GA 2026-05-19 at Google I/O. Google positioned it for agentic tool use. 1M input context. Rate card was 3x the 2.5-flash rate. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3.1-pro-preview:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Standard paid-tier pricing for prompts <=200K tokens. Same rate card as -customtools."
      gemini-3.1-pro-preview-customtools:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Standard paid-tier pricing for prompts <=200K tokens. Omitted from the comparison table because it matches gemini-3.1-pro-preview."
      gemini-3-pro-preview:
        actual_price:
          input_per_1m: 2.00
          output_per_1m: 12.00
          cache_read_per_1m: 0.20
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Google pricing page rate card reviewed 2026-04-02. Model availability and lifecycle are maintained in model_catalog.py."
      gemini-3-flash-preview:
        actual_price:
          input_per_1m: 0.50
          output_per_1m: 3.00
          cache_read_per_1m: 0.05
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Standard paid-tier pricing."
      gemini-3.1-flash-lite-preview:
        actual_price:
          input_per_1m: 0.25
          output_per_1m: 1.50
          cache_read_per_1m: 0.025
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-04-02'
        notes: "Standard paid-tier pricing. Same rate card as gemini-3.1-flash-lite (GA)."
      gemini-3.1-flash-lite:
        actual_price:
          input_per_1m: 0.25
          output_per_1m: 1.50
          cache_read_per_1m: 0.025
        cost_source: computed
        source_url: 'https://ai.google.dev/gemini-api/docs/pricing'
        last_reviewed: '2026-05-23'
        notes: "Reviewed 2026-05-23. Gemini 3.1 Flash-Lite was GA 2026-05-07. Identical rate card to gemini-3.1-flash-lite-preview. Model availability and lifecycle are maintained in model_catalog.py."

  deepseek:
    models:
      deepseek-v4-pro:
        actual_price:
          input_per_1m: 0.435
          output_per_1m: 0.87
          cache_read_per_1m: 0.003625
        list_price:
          input_per_1m: 1.74
          output_per_1m: 3.48
          cache_read_per_1m: 0.0145
        cost_source: computed
        source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        list_source_url: 'https://api-docs.deepseek.com/news/news260424'
        last_reviewed: '2026-05-23'
        notes: "DeepSeek V4 Pro (1.6T total / 49B active) via direct API. 1M context, 384K max output. Released 2026-04-24. actual_price reflects the 75% preview promo, EXTENDED to 2026-05-31 15:59 UTC (was 2026-05-05). list_price is the post-promo rate. Same model ID handles thinking and non-thinking modes. CACHE PRICE UPDATE: effective 2026-04-26 12:15 UTC, DeepSeek dropped cache-read to ~1/100 of cache-miss (was ~1/10); cache_read_per_1m updated accordingly."
      deepseek-v4-flash:
        actual_price:
          input_per_1m: 0.14
          output_per_1m: 0.28
          cache_read_per_1m: 0.0028
        cost_source: computed
        source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-05-23'
        notes: "DeepSeek V4 Flash (284B total / 13B active) via direct API. 1M context, 384K max output. No introductory promo. Same model ID handles thinking and non-thinking modes. Cache-read reduced 2026-04-26 to ~1/50 of cache-miss (was ~1/5)."

  moonshot:
    models:
      kimi-k2.6:
        actual_price:
          input_per_1m: 0.95
          output_per_1m: 4.00
          cache_read_per_1m: 0.16
        cost_source: computed
        source_url: 'https://platform.moonshot.ai/'
        last_reviewed: '2026-04-27'
        notes: "Moonshot direct-API pricing reviewed 2026-04-27. The exact current model ID and availability were not established in the 2026-09-10 model catalog review."

  vertex-maas:
    models:
      glm-5-maas:
        actual_price:
          input_per_1m: 1.00
          output_per_1m: 3.20
          cache_read_per_1m: 0.10
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        last_reviewed: '2026-04-02'
        notes: "Vertex AI published pay-as-you-go pricing. Google's public no-charge note expired on 2026-02-19. No separate direct-vendor list_price is stored until a current Zhipu public rate can be independently verified."
      glm-4.7-maas:
        actual_price:
          input_per_1m: 0.60
          output_per_1m: 2.20
          cache_read_per_1m: 0.06
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        last_reviewed: '2026-04-17'
        notes: "Vertex AI published pay-as-you-go pricing (verify against https://cloud.google.com/vertex-ai/generative-ai/pricing when reviewing)."
      kimi-k2-thinking-maas:
        actual_price:
          input_per_1m: 0.60
          output_per_1m: 2.50
          cache_read_per_1m: 0.06
        list_price:
          input_per_1m: 0.60
          output_per_1m: 2.50
          cache_read_per_1m: 0.15
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        list_source_url: 'https://platform.moonshot.ai/'
        last_reviewed: '2026-04-02'
        notes: "Vertex AI pay-as-you-go pricing vs Moonshot Kimi API pricing."
      deepseek-v3.2-maas:
        actual_price:
          input_per_1m: 0.56
          output_per_1m: 1.68
          cache_read_per_1m: 0.056
        list_price:
          input_per_1m: 0.14
          output_per_1m: 0.28
          cache_read_per_1m: 0.0028
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        list_source_url: 'https://api-docs.deepseek.com/quick_start/pricing/'
        last_reviewed: '2026-05-23'
        notes: "Vertex AI pay-as-you-go pricing for V3.2 vs DeepSeek's direct-API rate card. The direct V3.2 surface retires 2026-07-24 and is rolled into the V4-Flash alias chain, so list_price tracks V4-Flash rates ($0.14 / $0.28 / $0.0028) rather than the retiring V3.2 numbers."
      qwen3-235b-a22b-instruct-2507-maas:
        actual_price:
          input_per_1m: 0.22
          output_per_1m: 0.88
        list_price:
          input_per_1m: 0.287
          output_per_1m: 0.92
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        list_source_url: 'https://www.alibabacloud.com/help/en/model-studio/model-pricing'
        last_reviewed: '2026-04-02'
        notes: "Vertex AI pay-as-you-go pricing vs Alibaba Cloud Model Studio Global deployment mode pricing."
      qwen3-coder-480b-a35b-instruct-maas:
        actual_price:
          input_per_1m: 0.22
          output_per_1m: 1.80
          cache_read_per_1m: 0.022
        list_price:
          input_per_1m: 0.861
          output_per_1m: 3.441
        cost_source: computed
        source_url: 'https://cloud.google.com/vertex-ai/generative-ai/pricing'
        list_source_url: 'https://www.alibabacloud.com/help/en/model-studio/model-pricing'
        last_reviewed: '2026-04-02'
        notes: "Vertex AI pay-as-you-go pricing vs Alibaba Cloud Model Studio Global deployment mode pricing for the 0-32K input tier."
---
# Model Pricing Reference

All prices are **per 1M tokens in USD**. Used by metaproc to compute costs when a CLI
adapter does not self-report cost (for example Gemini CLI, or Pi CLI runs against Vertex
AI MaaS models).
For adapters that self-report cost (for example Claude CLI via Anthropic
API), the self-reported value still takes precedence for `actual_cost`.

## All Models — List Price Comparison

Sorted by output cost descending.
List price = the public vendor-direct rate when we have a separate, independently
verifiable vendor price.
When `list_price` is absent, `list_cost` falls back to `actual_price`.

The **Norm** column shows each model’s input price as a multiple of $0.28 / 1M tokens —
the legacy `deepseek-chat` baseline used when the Norm column was introduced.
The baseline is held constant so Norm values stay stable across pricing refreshes, even
after `deepseek-chat` itself was retired as a separate row.

| Model | Provider | Input | Norm | Output | Cache Read | Cache Write | Source |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| gpt-5.5-pro | OpenAI | $30.00 | 107.14x | $180.00 | -- | -- | [openai](https://openai.com/api/pricing) |
| gpt-5.5 | OpenAI | $5.00 | 17.86x | $30.00 | $0.50 | -- | [openai](https://openai.com/api/pricing) |
| claude-opus-4-7 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-4-6 | Anthropic | $5.00 | 17.86x | $25.00 | $0.50 | $6.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-4-6 | Anthropic | $3.00 | 10.71x | $15.00 | $0.30 | $3.75 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-4-5 | Anthropic | $3.00 | 10.71x | $15.00 | $0.30 | $3.75 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gemini-3.1-pro-preview | Google | $2.00 | 7.14x | $12.00 | $0.20 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3-pro-preview | Google | $2.00 | 7.14x | $12.00 | $0.20 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3.5-flash | Google | $1.50 | 5.36x | $9.00 | $0.15 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3.6-flash | Google | $1.50 | 5.36x | $7.50 | $0.15 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| claude-haiku-4-5 | Anthropic | $1.00 | 3.57x | $5.00 | $0.10 | $1.25 | [anthropic](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-5.4-mini | OpenAI | $0.75 | 2.68x | $4.50 | $0.075 | -- | [openai](https://openai.com/api/pricing) |
| kimi-k2.6 | Moonshot | $0.95 | 3.39x | $4.00 | $0.16 | -- | [moonshot](https://platform.moonshot.ai/) |
| deepseek-v4-pro | DeepSeek | $1.74 | 6.21x | $3.48 | $0.0145 | -- | [deepseek](https://api-docs.deepseek.com/news/news260424) |
| qwen3-coder-480b | Vertex MaaS | $0.861 | 3.08x | $3.441 | -- | -- | [alibaba](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| glm-5 | Vertex MaaS | $1.00 | 3.57x | $3.20 | $0.10 | -- | [vertex](https://cloud.google.com/vertex-ai/generative-ai/pricing) |
| gemini-3-flash-preview | Google | $0.50 | 1.79x | $3.00 | $0.05 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| kimi-k2-thinking | Vertex MaaS | $0.60 | 2.14x | $2.50 | $0.15 | -- | [moonshot](https://platform.moonshot.ai/) |
| glm-4.7-maas | Vertex MaaS | $0.60 | 2.14x | $2.20 | $0.06 | -- | [vertex](https://cloud.google.com/vertex-ai/generative-ai/pricing) |
| gemini-3.1-flash-lite-preview | Google | $0.25 | 0.89x | $1.50 | $0.025 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-3.1-flash-lite | Google | $0.25 | 0.89x | $1.50 | $0.025 | -- | [google](https://ai.google.dev/gemini-api/docs/pricing) |
| gpt-5.4-nano | OpenAI | $0.20 | 0.71x | $1.25 | $0.02 | -- | [openai](https://openai.com/api/pricing) |
| qwen3-235b | Vertex MaaS | $0.287 | 1.03x | $0.92 | -- | -- | [alibaba](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| deepseek-v4-flash | DeepSeek | $0.14 | 0.50x | $0.28 | $0.0028 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |
| deepseek-v3.2 | Vertex MaaS | $0.14 | 0.50x | $0.28 | $0.0028 | -- | [deepseek](https://api-docs.deepseek.com/quick_start/pricing/) |

## Provider Notes

### OpenAI

**GPT-5.4 family reviewed 2026-04-14** against OpenRouter’s `openai/*` mirror.

The retained Mini and Nano rows record that review’s rates.

OpenAI’s documented cache pricing formula is input ÷ 10 (90% cached-input discount) for
the GPT-5 family. Treat the formula as a sanity check and verify every stored rate
against a current primary source.

**ChatGPT-OAuth dispatch (Vehicle B, via codex-cli).** When a run dispatches through
codex-cli using a ChatGPT subscription OAuth session (`~/.codex/auth.json` with
`auth_mode: chatgpt`), billing is subscription-flat (free-per-request, bundled into the
monthly subscription fee) — **not** metered at the rates above.
The same is true of the Claude-CLI personal-plan dispatch path.
The metered rates in this file apply only to `OPENAI_API_KEY` (Vehicle A) dispatch.

Cost source: `computed` (no adapter in metaproc self-reports OpenAI cost today).
`list_cost` falls back to the same rate card because OpenAI models in this file do not
have a separate `list_price` block.

### Anthropic

**Verified 2026-04-02** against Anthropic’s public
[pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

Anthropic’s prompt-caching multipliers are public: cache reads are `0.1x` base input,
5-minute cache writes are `1.25x`, and 1-hour cache writes are `2x`. This file stores
the 5-minute cache-write rate in `cache_write_per_1m`, because the usage logs currently
do not distinguish 5-minute from 1-hour writes.

Cost source: `self-reported` for Claude CLI actual cost.
`list_cost` falls back to the same public API rate card because Anthropic models in this
file do not have a separate `list_price` block.

### Google AI (Gemini)

**Verified 2026-07-24** against Google’s public
[Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini changelog](https://ai.google.dev/gemini-api/docs/changelog), and
[Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3).

**July 2026 additions:**

- `gemini-3.6-flash` — GA 2026-07-21. The standard paid tier is $1.50/M input and
  $7.50/M output, with medium default thinking, 1M input context, and 64K maximum
  output.

**May 2026 additions:**
- `gemini-3.5-flash` — GA 2026-05-19 at Google I/O. Positioned by Google as the
  agentic-Flash tier (shift from “speed” to “autonomy”) and better at tool calling than
  `gemini-3-flash-preview`. $1.50/$9.00 — 3x the 2.5-Flash rate but Google says
  intelligence/agentic gains justify it.
- `gemini-3.1-flash-lite` — GA 2026-05-07 (preview → stable).
  Same rate card as `gemini-3.1-flash-lite-preview`.

Current model availability, lifecycle, and route evidence are maintained in
`model_catalog.py`; this file records pricing evidence only.

This file stores the standard paid tier for the listed model IDs.
For `gemini-3.1-pro-preview`, `gemini-3.1-pro-preview-customtools`, and
`gemini-3-pro-preview`, that means the published rate for prompts `<=200K` tokens.

For the cached-input prices Google publishes on the pricing page, the cache-read rate is
`0.1x` the standard input rate for the models in this file.

The `gemini-3.1-pro-preview-customtools` model is intentionally omitted from the
comparison table because it has the same standard rate card as `gemini-3.1-pro-preview`.

The Gemini Developer API free tier is model-specific, not universal.
As of 2026-04-02, Google’s public docs show free API tiers for `gemini-3-flash-preview`
and `gemini-3.1-flash-lite-preview`, while `gemini-3.1-pro-preview` does not have a
Gemini API free tier.

### DeepSeek (Direct API)

**Verified 2026-05-23** against DeepSeek’s public
[pricing page](https://api-docs.deepseek.com/quick_start/pricing/).

**May 2026 changes:**
- The V4-Pro 75%-off promo was **extended** from 2026-05-05 to 2026-05-31 15:59 UTC.
  Until then, V4-Pro bills at $0.435 input / $0.87 output; after that, list rates resume
  ($1.74 / $3.48).
- Effective **2026-04-26 12:15 UTC**, DeepSeek slashed input-cache pricing to ~1/100 of
  cache-miss (was ~1/10) for V4-Pro and V4-Flash.
  Updated `cache_read_per_1m` for both models in this file.
- The `deepseek-chat` (V3.2 non-thinking) and `deepseek-reasoner` (V3.2 thinking)
  direct-API aliases retire **2026-07-24**. `deepseek-reasoner` also lacks function
  calling per
  [DeepSeek’s reasoning-model docs](https://api-docs.deepseek.com/guides/reasoning_model).
  Both have been removed from this file; new process specs should use
  `deepseek-v4-flash` or `deepseek-v4-pro` directly.
  Historical run usage.md files that mention `deepseek-chat` will roll up at $0 cost
  until the retirement date.

Cost source: `computed` (no adapter in metaproc self-reports DeepSeek direct cost).
The V4-Flash rate card ($0.14 / $0.28 / $0.0028) is mirrored on the `deepseek-v3.2-maas`
Vertex MaaS entry’s `list_price` block — Vertex still hosts the V3.2 MaaS surface, but
the public direct-API comparison point is now the V4-Flash rollup.

### Vertex MaaS (Third-Party Models)

**Verified 2026-04-02** against Google’s public
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing).

These models are not blanket-free anymore.
Google now publishes pay-as-you-go token pricing for partner / MaaS models on the Vertex
AI pricing page.

`glm-5-maas` was the only model in this file with an explicit public no-charge note.
Google’s pricing page says GLM-5 was available at no charge until **February 19, 2026**,
so that promotion is expired as of **April 2, 2026**.

Where we have a separate, independently verifiable direct-vendor public rate, that rate
is stored in `list_price`. Where we do not, `list_cost` falls back to `actual_price`.
That is why `glm-5-maas` currently has no separate `list_price` block.

For Alibaba Qwen models, `list_price` is deployment-mode-specific.
This file currently uses Alibaba Cloud Model Studio’s **Global deployment mode**
pricing, and for `qwen3-coder-480b-a35b-instruct-maas` it uses the published **0-32K
input tier**.

## Google Credits and Promotions

**Verified 2026-04-02** against Google’s public startup and free-program docs.

- Standard Google startup credits are not “all models” credits.
  Google says program members can use standard startup credits on **Gemini** and
  **Vertex AI open models**. Source:
  [AI startup program](https://cloud.google.com/startup/ai).
- The additional partner-model startup credit is separate, and it is not limited to
  open-source models. Google says qualifying startups may receive an additional
  **$10,000** to use **partner LLM models** through Model Garden.
  Source: [AI startup program](https://cloud.google.com/startup/ai) and
  [Startup Perks](https://cloud.google.com/startup/perks).
- Google’s startup materials explicitly describe that partner-model credit as applying
  to partner models such as **AI21**, **Anthropic**, and **Mistral** in Model Garden.
  Source: [Startup Perks](https://cloud.google.com/startup/perks).
- The standard **$300 Free Trial** is narrower still.
  Google’s Free Trial docs say that the credit cannot be used for **Gemini Developer API
  / AI Studio** costs and cannot be used for a **generative AI partner model offered as
  a managed API / model as a service**. Source:
  [Free Google Cloud features and trial offer](https://docs.cloud.google.com/free/docs/free-cloud-features).
- For Anthropic on Google specifically, this review did **not** find any broader public
  Google-wide Claude discount beyond the startup partner-model credit above.
  Public docs show pay-as-you-go Claude pricing on Vertex plus sales-led custom quotes,
  not a blanket promotion.
  Sources:
  [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing),
  [AI startup program](https://cloud.google.com/startup/ai), and
  [Startup Perks](https://cloud.google.com/startup/perks).

## How Pricing Flows into Usage Reports

The `metaproc write-usage` command produces `usage.md` files with two cost columns:

- **actual_cost**: What was actually paid, or the best available computed estimate when
  the adapter does not report a cost.
- **list_cost**: What the same usage would cost at the vendor’s public rate card stored
  in this file.

### How each column is computed

| Provider | actual_cost | list_cost |
| --- | --- | --- |
| OpenAI | Computed from `actual_price` (no adapter self-reports OpenAI cost today) | Same as actual because OpenAI models in this file do not have a separate `list_price` block |
| Anthropic (Claude CLI, Pi CLI) | Uses self-reported `cost_usd` from the API response when present; otherwise computes from `actual_price` | Falls back to `actual_price` because Anthropic models in this file do not have a separate `list_price` block |
| Google (Gemini CLI) | Computed from `actual_price` because Gemini CLI does not report cost | Same as actual because Google models in this file do not have a separate `list_price` block |
| DeepSeek (direct API) | Computed from `actual_price` | Same as actual |
| Vertex MaaS (Pi CLI) | Computed from Vertex AI’s published `actual_price` when the adapter reports no cost | Computed from `list_price` when present, otherwise falls back to `actual_price` |

### Key logic in usage.py

- `cost_source: self-reported` means the CLI adapter reports `cost_usd` and that value
  is trusted for `actual_cost`. The pricing table is still used for `list_cost`.
- `cost_source: computed` means cost is always calculated from the pricing table rates.
- For `list_cost`, the code always computes from the table: it uses `list_price` if
  present, otherwise it falls back to `actual_price`.
- Org-prefixed model names (for example `zai-org/glm-5-maas` from Pi CLI) are resolved
  by `_lookup_model()` which strips the prefix automatically.

## Known Issues and Uncertainties

### ISSUE: actual_cost > list_cost for Anthropic models

**Status**: Under investigation.

**Symptom**: In usage.md reports, `actual_cost` for Claude CLI sessions can exceed
`list_cost` for the same token counts.

**Root cause**: `actual_cost` uses the self-reported `cost_usd` from Claude CLI, while
`list_cost` is computed from this pricing table.
The self-reported value is sometimes higher than what the table produces for the same
token counts.

**Possible explanations**:

1. Extended-thinking tokens may be billed in the self-reported total while the usage log
   token counts do not expose them separately.
2. The CLI may report costs using a rate basis or token accounting detail not visible in
   the log summary we ingest.
3. The token counts extracted from logs may not exactly match what the API billed for.

**How to verify**:

- Compare a single Claude CLI API response’s full `usage` payload against the log entry.
- Compute expected cost from every token category present in that API response.
- Check whether Claude CLI’s reported total includes extended-thinking or other hidden
  token buckets.

### NOTE: Vendor list prices may still be deployment-mode-specific

The `list_price` entries for Vertex MaaS models are exact only for the vendor deployment
mode, tier, and public page cited in `list_source_url`.

Current choices in this file:

- Moonshot: Kimi public API pricing.
- DeepSeek: public DeepSeek API pricing.
- Alibaba Qwen: Model Studio **Global deployment mode** pricing, with the
  `qwen3-coder-480b-a35b-instruct` row using the published **0-32K input tier**.

If you want a different vendor deployment mode, update both `list_price` and the note.

### NOTE: GLM-5 direct-vendor list pricing is intentionally omitted

The older GLM `list_price` block was removed because it could not be independently
verified from a current machine-readable public Zhipu pricing source during this review.
Until that source is available, `list_cost` for `glm-5-maas` falls back to Vertex AI’s
published public rate, which is exact and source-backed.

## Data Structure

Each model entry in the YAML frontmatter has an `actual_price` block:

- `input_per_1m` -- cost per 1M input tokens
- `output_per_1m` -- cost per 1M output tokens
- `cache_read_per_1m` -- cost per 1M cached input tokens (cache hits)
- `cache_write_per_1m` -- cost per 1M tokens written to cache

Models with a separately stored vendor-direct public rate also have a `list_price` block
with the same fields.
When `list_price` is absent, `list_cost` falls back to `actual_price`.

Additional per-model fields:

- `cost_source` -- `"self-reported"` (adapter reports cost) or `"computed"` (from
  pricing table)
- `source_url` -- official pricing page URL for `actual_price`
- `list_source_url` -- official vendor pricing page URL for `list_price`
- `last_reviewed` -- date of last verification (YYYY-MM-DD)
- `notes` -- free-text context about tiers, regions, or caveats

## How to Update This File

**Schedule:** Monthly, when adding a model to a process spec, or when a provider changes
its rates. Model availability and lifecycle are reviewed separately under
[Model Catalog Maintenance](../../../docs/project/model-catalog-maintenance.md).

### Update procedure

1. **Verify current rates** against the official provider pages listed below.
   Record the applicable API, region, context tier, cache policy, and effective date.
2. **Use secondary catalogs for drift detection.** Compare models.dev, OpenRouter, and
   pi-mono with this file, but do not treat them as rate authority.
3. **Resolve every delta** against a primary provider source.
   Leave the existing rate unchanged and record the uncertainty when no current primary
   source is available.
4. **Recheck Google credit and promotion docs** if this file makes any claims about free
   tiers, startup credits, or partner-model credits.
5. **Update the YAML frontmatter:** prices, notes, `last_reviewed` per model, and
   top-level `last_updated`.
6. **Update the markdown table and prose below** so it matches the frontmatter exactly.
   The table is sorted by output cost descending.
7. **Run the focused test:**
   `uv --config-file uv.toml run --frozen pytest tests/test_usage.py -q`.
8. **Run `make verify`** and confirm the pricing data is present in the built wheel.
9. **Verify on a recent run when available:** `metaproc write-usage <phase-dir>`.

### When to update

- When adding a new model to a process spec.
- When provider pricing changes.
- When Google changes startup-credit or free-tier scope in a way that affects the prose
  above.
- After a run shows “unknown model” warnings in `usage.md`.

### Official pricing sources (primary — use these)

- **OpenAI:** <https://openai.com/api/pricing>
- **Anthropic:** <https://platform.claude.com/docs/en/about-claude/pricing> and
  <https://claude.com/pricing>
- **Google Gemini:** <https://ai.google.dev/gemini-api/docs/pricing>
- **Google Gemini 3 developer guide:** <https://ai.google.dev/gemini-api/docs/gemini-3>
- **Vertex AI:** <https://cloud.google.com/vertex-ai/generative-ai/pricing>
- **DeepSeek:** <https://api-docs.deepseek.com/quick_start/pricing/>
- **DeepInfra:** <https://deepinfra.com/pricing>
- **Moonshot / Kimi:** <https://platform.moonshot.ai/>
- **Alibaba Model Studio:**
  <https://www.alibabacloud.com/help/en/model-studio/model-pricing>
- **xAI:** <https://docs.x.ai/docs/models>

### Cross-reference sources (secondary — for validation)

- **models.dev:** <https://models.dev/api.json> — use the `firmware` provider key.
- **OpenRouter:** <https://openrouter.ai/api/v1/models>
- **pi-mono:** <https://github.com/badlogic/pi-mono> — maintains manual corrections for
  upstream pricing errors across 700+ models.

### Google-specific credit and free-tier sources

- Google AI startup program: <https://cloud.google.com/startup/ai>
- Google Startup Perks: <https://cloud.google.com/startup/perks>
- Google Cloud Free Trial / Free Program:
  <https://docs.cloud.google.com/free/docs/free-cloud-features>

### Cache-pricing formulas by provider

These formulas are useful sanity checks when filling in `cache_read_per_1m` for a new
model:

| Provider | Cache-read formula |
| --- | --- |
| OpenAI | input ÷ 10 (90% discount) for the GPT-5 family; ~50% for the o-series |
| Anthropic | cache read = input ÷ 10 (90%); 5-minute cache write = input × 1.25; 1-hour cache write = input × 2 |
| Google | input ÷ 10 (90% discount) for Gemini 2.5 and 3.x; input ÷ 4 (75%) for Gemini 2.0 |
| xAI | input ÷ 4 (75% discount) for most models |
| DeepSeek | input ÷ 10 (90% discount) |

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
