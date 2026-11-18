# Research: Model Catalog and Agent CLI Refresh (2026-11-18)

**Date:** 2026-11-18

**Status:** Complete; implemented on the November refresh branch (bead `mp-5212`, epic
`mp-qmr0`).

## Overview

This record preserves the evidence behind the November model-catalog review and the
upgrade of the four pinned agent CLIs.
It keeps the four evidence boundaries of
[Model Catalog Maintenance](../model-catalog-maintenance.md) separate: catalog existence,
CLI parsing, provider availability, and live compatibility.
Live probes ran on 2026-11-18 from a cloud development container through the routes
named in each section; no Vertex AI credential was available, so every Vertex route is
unverified.

## Pin Selection

The 14-day cool-off cutoff was 2026-11-04T01:15Z.

| CLI | Previous pin | New pin | Published | Basis |
| --- | --- | --- | --- | --- |
| Claude Code | 2.1.234 | 2.1.325 | 2026-11-13 | Owner exemption: Fable 5.2 needs 2.1.321 |
| Codex | 0.147.0 | 0.166.0 | 2026-10-29 | Newest outside the window |
| Gemini CLI | 0.59.0 | 0.66.0 | 2026-10-27 | Newest outside the window |
| Pi | 0.84.2 | 1.5.2 | 2026-11-10 | Owner exemption: Fable 5.2 needs 1.5.1 |

No advisory is fixed only after Codex 0.166.0 or Gemini CLI 0.66.0. Claude Code 2.1.234
was affected by two published advisories (GHSA-5xjx-9qx6-jcrv, High, fixed in 2.1.251;
GHSA-gfvf-j8jh-jxxw, Low, fixed in 2.1.260). A Pi 0.84.2 dependency tree reported seven
`npm audit` findings, two high; the gated 1.5.2 tree reports none.
The exemption reviews are recorded in
[SUPPLY-CHAIN-SECURITY.md](../../../SUPPLY-CHAIN-SECURITY.md#audited-third-party-cool-off-exceptions).

## Google

Sources: [models](https://aistudio.google.com/docs/models),
[deprecations](https://aistudio.google.com/docs/deprecations),
[thinking](https://aistudio.google.com/docs/thinking),
[Vertex model versions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions),
[Vertex open-model deprecations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models).
The Gemini API documentation now redirects from `ai.google.dev` to
`aistudio.google.com`.

### Gemini 4

| ID | Released | Thinking levels (default) | Notes |
| --- | --- | --- | --- |
| `gemini-4-argon` | 2026-10-13 | low, medium, high (medium) | Vertex availability until at least 2027-10-13 |
| `gemini-4-carbon` | 2026-11-10 | low, medium, high (medium) | Strongest model; paid tier only |
| `gemini-4-hydrogen` | 2026-11-12 | low through xhigh (high) | Google’s recommended upgrade from 3.8 Flash |

All three are GA with a 1,048,576-token shared context, reject `minimal` and
`thinking_budget` with HTTP 400, and support implicit caching only.
The owner’s request named “Argon” and “Kai Run”; Argon is `gemini-4-argon`, and no
Google model or codename matches “Kai Run”, so Carbon is the presumed intent.

### Gemini 3 Lifecycle

- On the Gemini API, `gemini-3.5-flash` and `gemini-3.7-flash` were deprecated
  2026-10-08 and auto-route to 3.6 Flash and 3.8 Flash.
- On Vertex, `gemini-3.6-flash` retires 2026-11-19 (replacement `gemini-3.8-flash`),
  `gemini-3.7-flash` retires 2027-01-28, and `gemini-3.5-flash` stays GA until at least
  2027-05-19.
- `gemini-3.1-pro-preview-customtools` is a documented public-preview endpoint on both
  surfaces with a 1,048,576-token context, not the 2,097,152 previously packaged for
  Pi.
- The owner’s operator comparisons rated 3.8 Flash below 3.6 Flash, so the refresh
  retires 3.6 and 3.5 Flash profiles in favor of Gemini 4 Hydrogen rather than 3.8.

### Vertex MaaS

All six packaged open-model IDs (GLM 5, GLM 4.7, DeepSeek V3.2, Kimi K2 Thinking, and two
Qwen3 models) retired on 2026-10-21. Current replacements are preview models in the
`global` region only: `zai-org/glm-5.2-maas`, `deepseek-ai/deepseek-v4.1-flash-maas`
and `deepseek-ai/deepseek-v4.1-flash-1017-maas`. Vertex MaaS currently serves no Kimi
or Qwen chat model.

### Gemini CLI 0.59.0 to 0.66.0

Read from source at tags `v0.59.0` (`fb0d535a`) and `v0.66.0` (`97041320`):

- 0.61.0 removed the rewrite of unrecognized `*flash` IDs, which was the reason
  Metaproc enabled `experimental.dynamicModelConfiguration`.
- 0.62.0 added forced remaps under Vertex, API-key and gateway auth:
  `gemini-3.5-flash` and `gemini-3-flash` are sent as `gemini-3.8-flash`, and
  `gemini-3.1-flash-lite` as `gemini-3.5-flash-lite`, on both resolution paths.
- With dynamic resolution on, 0.66.0 sent an explicit `gemini-3.1-pro-preview` as
  `-customtools` under API-key auth (observed live), so Metaproc now asserts it off.
- The `modelConfigs.overrides` entry matching `overrideScope: "chat-base-3"` never
  applied in 0.59.0 or 0.66.0: `overrideScope` holds only sub-agent names.
  Matching `model` applies the thinking level.
- No release through 0.69.0-preview.0 knows a `gemini-4-*` ID; they pass through
  unchanged.
- `general.sessionRetention.enabled: false` is still required: the unbounded
  session-file `Promise.all` scan is still present.

Live probes on the Gemini API-key route (one shell tool call each): Gemini 4 Argon,
Carbon and Hydrogen, 3.8 Flash, 3.6 Flash, 3.1 Pro preview and `-customtools`, and the
`flash`, `pro`, `flash-lite` and `auto` aliases all served the expected model and
completed the call.

## OpenAI and Codex

Sources: [Codex models](https://learn.chatgpt.com/docs/models),
[API models](https://developers.openai.com/api/docs/models),
[deprecations](https://developers.openai.com/api/docs/deprecations), and the Codex source
at `rust-v0.166.0`.

- Current IDs added: `gpt-6.2-astra` and `gpt-6.2-sol` (2026-11-17, absent from the
  0.166.0 presets), `gpt-6.1-sol` (2026-09-29), `gpt-6.1-terra`, `gpt-6-sol`,
  `gpt-6-luna`.
- `gpt-5.5` retired from Codex with ChatGPT sign-in on 2026-10-14 and remains
  API-key-only; `gpt-5.4`, `gpt-5.4-mini` and `gpt-5.3-codex-spark` are retired in
  Codex. The codex-cli default moved to `gpt-6.1-sol`.
- Codex removed `-a on-failure` in 0.143.0 and `-a untrusted` in 0.149.0; `codex exec`
  forces approval `never` in headless mode, so `-a` is only parse-validated.
- Codex writes `auth_mode` at the top level of `auth.json`; Metaproc read only the nested
  `tokens.auth_mode`, so real ChatGPT login files were rejected.
- `codex exec` reads an API key only from `CODEX_API_KEY`; an `OPENAI_API_KEY`-only run
  failed with HTTP 401 at 0.147.0 and 0.166.0.
- Usage events report `cache_write_input_tokens` inside `input_tokens`.
- `--json` prints no config preamble and reports no served model; the session rollout
  records the requested model.
- The new `persistent` effort parses, but the API rejected it for `gpt-6.1-sol`.

Live probes on the API-key route (adapter argv, one shell tool call each): `gpt-6.1-sol`,
`gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6-luna` and `gpt-5.5` completed.
The ChatGPT sign-in route was not probed.

DeepSeek now serves `deepseek-flash` (V4.1 Flash) and bills peak and off-peak rates;
`deepseek-v4-flash` is a retired alias.
Moonshot’s `kimi-k2.6` is closed to new users from 2026-11-01 with deprecation expected
late December 2026; `kimi-k3` and `kimi-k3.1-pro` are current.

## Anthropic and Claude Code

Sources: [models](https://platform.claude.com/docs/en/models/overview),
[deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations),
[Claude Code model configuration](https://code.claude.com/docs/en/model-config).

- Current lineup: Fable 5.2 (2026-11-09, Claude Code 2.1.321+), Opus 5.5 (2026-09-22,
  2.1.280+), Sonnet 5.5 (2026-09-28, 2.1.284+), Haiku 5.5 (2026-10-07, 2.1.293+). Sonnet
  4.5 retires 2026-11-30.
- On the new pin the moving aliases resolve to `opus` → Opus 5.5, `sonnet` → Sonnet 5.5,
  `haiku` → Haiku 5.5, `fable` → Fable 5.2. The claude-code-cli default is `opus`, so
  the default model moves from Opus 5 to Opus 5.5; the owner accepted that move.
- The `-d api` token Metaproc passed never filtered the debug log, and the filtering form
  `--debug=api` drops the uncategorized API error lines the failure classifier reads.
- Anthropic profile and Workload Identity Federation variables outrank stored `/login`
  credentials.
- Claude Code 2.1.325 writes cache entries with a one-hour TTL; its own
  `total_cost_usd` prices them correctly.

Live probes on the Anthropic first-party route (Claude Code 2.1.325, one Bash tool call
each): `claude-fable-5-2`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`,
`opus` and `fable` served the expected model.
Pooled-slot credential vehicles were not exercised.

## Pi

Read from the 0.84.2, 1.5.0, 1.5.1 and 1.5.2 tarballs without executing package code
except the pinned CLI:

- The adapter’s flags, JSON events, `--version` output, config directory and
  `models.json` schema are compatible.
- 1.x enables built-in MCP, codemode and tool-search extensions; `--no-mcp` restores
  0.84.2’s tool isolation.
- Since Pi 0.77 a bare `apiKey` string in `models.json` is a literal key that outranks
  the environment. The packaged file’s `"OPENAI_API_KEY"`, `"DEEPSEEK_API_KEY"` and
  `"MOONSHOT_API_KEY"` were therefore sent as the key (observed live: HTTP 401).
- A custom `models.json` entry for a model Pi ships natively replaces the native entry,
  zeroing its cost and dropping its thinking map.
- 1.5.1 turns Anthropic message threads on by default and changes cache-warming
  defaults; the packaged data turns threads off, and idle cache warming is inert in
  `-p` mode.
- `vertex` and `bedrock` were never Pi provider IDs.

Live probes (adapter argv, one bash tool call each): `gpt-6.1-sol`, `gpt-6.2-sol` and
`gpt-5.5` on `openai`, and `gemini-4-argon` on Pi’s `google` provider, completed.
Anthropic, `google-vertex` and `vertex-maas` were not probed.

## Open Questions

- Vertex routes for Gemini 4, Gemini 3.1 Pro and the MaaS replacements, and the Codex
  ChatGPT sign-in route, need bounded probes with production credentials.
- The Vertex MaaS replacements carry only a 45-day preview minimum (about 2026-11-28 to
  2026-11-30).
- Gemini 4 Argon and Carbon and GPT-5.6 Sol promotional prices end 2026-11-30 (bead
  `mp-qshs`).
- Whether the gemini-cli default should move to `gemini-4-argon` awaits a Vertex
  tool-call probe and a tier-1 dry run.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
