"""Centralized model and adapter defaults.

Model names and valid-model sets are overridable via process spec
config so projects can set their own defaults.
"""

from __future__ import annotations

import os as _os

# ── GCP ─────────────────────────────────────────────────────────
# Proactive token refresh margin (minutes).  GCP access tokens have a 60-minute
# TTL.  Refreshing this many minutes before expiry ensures subprocesses launched
# with the token have at least (60 - margin) minutes of headroom.
GCP_TOKEN_REFRESH_MARGIN_MINUTES = 10

# ── Pool concurrency ───────────────────────────────────────────
#
# The adaptive pool manages concurrency in three phases:
#
#   1. STARTUP: estimate initial concurrency from free memory.
#      initial = min(free_memory × 50% / POOL_ESTIMATED_PROCESS_RSS_BYTES,
#                    max_concurrency)
#      RunPool fails clearly if required host memory telemetry cannot be read.
#      Specs can override the process estimate with estimated_process_rss_mb
#      and the initial budget with initial_memory_budget_fraction for heavier
#      workload profiles.
#      See estimate_initial_concurrency() in osutils/memory_pressure.py.
#
#   2. RAMP-UP: every monitor tick (10 s) with 3 consecutive NORMAL pressure
#      readings, the pool increases concurrency by 10% toward max.
#
#   3. BACK-OFF: ELEVATED holds current concurrency; HIGH/CRITICAL pressure
#      reduces concurrency by 20%/50%.  It never drops below min.
#
# Example initial values (500 MB per process, 50% of free memory):
#   16 GB laptop (40% free = 6 GB)  →  6
#   32 GB machine (80% free = 25 GB) → 25
#   64 GB VM (90% free = 58 GB)     → 58
#   128 GB VM (90% free = 115 GB)   → 117
#
POOL_MAX_CONCURRENCY = 200
POOL_INITIAL_CONCURRENCY = 20  # Event-schema default only; runtime auto-estimates.
# Raised 1 → 2 (2026-05-24): a min_concurrency floor of 1 lets the adaptive
# controller ratchet down to a degenerate state under sustained pressure.
# Per the 2026-05-24 three-lane Memorial batch logbook entry [Execution-1],
# multiple parallel orchestrators all independently ratcheted to 1 and the
# fleet throughput collapsed to ~1/N of expected. A floor of 2 keeps at
# least minimal parallelism per pool while still respecting pressure signals.
# Per-profile override available via the profile YAML `resources.min_concurrency`.
POOL_MIN_CONCURRENCY = 2

# Conservative estimate of per-agent-process RSS in bytes.  Used by
# estimate_initial_concurrency() to compute how many processes can fit in
# available memory.  pi-cli processes typically use 300-600 MB depending on
# model, prompt size, and streaming response buffers.  500 MB avoids
# over-committing on smaller machines while still allowing efficient startup
# on large VMs.
POOL_ESTIMATED_PROCESS_RSS_BYTES = 500 * 1024 * 1024  # 500 MB

# ── Pool process health ─────────────────────────────────────────
# Stall detection: kill a pool process if its log file stops growing for this
# many minutes.  This catches hung processes (e.g. pi-cli stuck on a dead TCP
# connection after a 401) without imposing a hard upper bound on legitimate
# long-running agents.  Set to None to disable stall detection.
#
# Overridable via env var POOL_STALL_TIMEOUT_MINUTES.
#
# Default raised from 5 → 15 minutes (2026-05-26) after the Tue production batch:
# claude-opus agents legitimately go silent for 5-10 min during long internal
# reasoning passes, deep WebFetches, and tool-call sequences. The previous
# 5-minute default was killing in-progress claude agents 8+ times in a single
# batch run, each kill triggering a full retry attempt (5-20 min wasted per
# kill). 15 minutes is long enough to absorb claude-opus's natural silent
# windows while still catching the original target (genuinely hung processes
# after a 401 or TCP dead-stop). Workflows that need a tighter bound for
# diagnostic runs can set POOL_STALL_TIMEOUT_MINUTES=5 explicitly.
#
# See `metaproc help operator` § "Top mistakes to avoid" row about stall
# kills and the production incident analysis for the root-cause analysis.
_pool_stall_env = _os.environ.get("POOL_STALL_TIMEOUT_MINUTES")
POOL_STALL_TIMEOUT_MINUTES: int | None = (
    int(_pool_stall_env)
    if _pool_stall_env and _pool_stall_env.lower() not in ("none", "null", "")
    else 15
)

# ── Claude ────────────────────────────────────────────────────────

CLAUDE_DEFAULT_MODEL = "opus"
CLAUDE_DEFAULT_EFFORT = "high"

CLAUDE_VALID_MODELS: set[str] = {
    "opus",
    "sonnet",
    "haiku",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}

# ── Gemini ────────────────────────────────────────────────────────

GEMINI_DEFAULT_MODEL = "gemini-3.1-pro-preview-customtools"
GEMINI_DEFAULT_THINKING_LEVEL = "HIGH"

GEMINI_VALID_MODELS: set[str] = {
    # Gemini 3.6 — GA July 2026. Current stable Flash model for agentic
    # workflows, with lower output pricing than 3.5 Flash.
    "gemini-3.6-flash",
    # Gemini 3.5 — GA May 2026. 3.5 Flash is positioned by Google as
    # "shifting Flash from speed to autonomy" (better at agentic / tool-use
    # workflows than the 3.0/3.1 Flash previews). Released 2026-05-19 at I/O.
    "gemini-3.5-flash",
    # Gemini 3.1 family
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-3.1-flash-lite",  # GA 2026-05-07
    "gemini-3.1-flash-lite-preview",
    # Gemini 3.0 (older previews — kept for backward compat with stored runs)
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    # Shorthand aliases pi-cli / gemini-cli resolve internally.
    "auto",
    "auto-gemini-3",
    "pro",
    "flash",
    "flash-lite",
}

# ── Codex ────────────────────────────────────────────────────────
#
# codex-cli (OpenAI Codex CLI, npm @openai/codex) defaults. The adapter
# dispatches through `codex exec --json`; see
# src/metaproc/docs/metaproc-design.md
# for design context and the empirical 0.124.0 surface.

CODEX_DEFAULT_MODEL = "gpt-5.5"
CODEX_DEFAULT_EFFORT = "medium"

# Whitelist for the Codex adapter. Per OpenAI's Codex CLI docs as of
# 2026-04-27: codex CLI uses `gpt-5.5` (primary), `gpt-5.4` (fallback for
# accounts without 5.5 access), and `gpt-5.4-mini` (light tasks / subagents).
# `gpt-5.5-pro` is included for high-reasoning runs. Older IDs (gpt-5.2,
# gpt-5.2-codex, gpt-5.3-codex, o3, o4-mini) were trimmed per `only support
# latest`. Restore from git history if a historical run requires
# re-validation.
CODEX_VALID_MODELS: set[str] = {
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-mini",
}

# `codex exec --sandbox <MODE>` — the three upstream-documented modes.
CODEX_VALID_SANDBOX_MODES: set[str] = {
    "read-only",
    "workspace-write",
    "danger-full-access",
}

# `codex -a <POLICY>` — top-level flag. `on-failure` is deprecated in
# codex-cli 0.124.0 but still accepted; we keep it for the `acceptEdits`
# permission mode until upstream removes it.
CODEX_VALID_APPROVAL_POLICIES: set[str] = {
    "untrusted",
    "on-failure",
    "on-request",
    "never",
}

# `-c model_reasoning_effort=<EFFORT>` — codex-cli config-path override.
CODEX_VALID_EFFORTS: set[str] = {
    "minimal",
    "low",
    "medium",
    "high",
}

# ── Pi ───────────────────────────────────────────────────────────

PI_DEFAULT_MODEL = "sonnet"
PI_DEFAULT_PROVIDER = "anthropic"

PI_VALID_MODELS: set[str] = {
    # Anthropic
    "sonnet",
    "opus",
    "haiku",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
    # OpenAI — current tiers only. GPT-5.5 (released 2026-04-23, API
    # 2026-04-24) is the flagship; gpt-5.4-mini / gpt-5.4-nano are the
    # current cost-optimized tiers (gpt-5.5-mini is projected Q3 2026).
    # gpt-5.5-pro is intentionally absent: it requires OpenAI's
    # /v1/responses endpoint and pi-cli's `openai-completions` api
    # cannot dispatch it (returns "404 not a chat model"). gpt-5.5-pro
    # is still in CODEX_VALID_MODELS — codex-cli handles responses
    # internally — and in pricing.md for cost rollups.
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    # Google
    "gemini-3.1-pro-preview",
    # Vertex AI — Gemini via google-vertex native API (pi-mono @google/genai SDK).
    # Migrated 2026-04-18 from openai-completions to google-vertex so Gemini 3
    # preview thought_signature round-trip works — see
    # metaproc/docs/runbooks/adapter-compatibility.runbook.md.
    # Naked IDs (no "google/" prefix) — that's the form pi-mono's google-vertex
    # provider uses. Keep these strings in sync with pi-models.default.json
    # and with consuming process-spec adapter maps. Mismatches here cause the
    # adapter to silently fall back to PI_DEFAULT_MODEL ("sonnet") and
    # retry-storm every item — see commit fixing 1d-r4 / 1e-r4 / 1ect-r4.
    # gemini-3.5-flash is the GA agentic-Flash from I/O 2026-05-19.
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview-customtools",
    # Vertex AI MaaS (via openai-completions in models.json)
    "glm-5-maas",
    "glm-4.7-maas",
    "kimi-k2-thinking-maas",
    "deepseek-v3.2-maas",
    "qwen3-235b-a22b-instruct-2507-maas",
    "qwen3-coder-480b-a35b-instruct-maas",
    # Vertex MaaS models referenced by fully-qualified publisher IDs
    # in pi-models.default.json. Both short names (above) and long
    # forms resolve under pi-cli's lookup, so accept either here.
    "zai-org/glm-5-maas",
    "zai-org/glm-4.7-maas",
    "moonshotai/kimi-k2-thinking-maas",
    "deepseek-ai/deepseek-v3.2-maas",
    "qwen/qwen3-235b-a22b-instruct-2507-maas",
    "qwen/qwen3-coder-480b-a35b-instruct-maas",
    # DeepSeek first-party API (V4 family). Released 2026-04-24.
    # Both IDs support 1M context and dual mode (thinking /
    # non-thinking) on the same model ID. See
    # src/metaproc/docs/metaproc-design.md.
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    # Moonshot first-party API (Kimi K2.6). GA April 2026,
    # multimodal (text + image). Vertex MaaS still only carries
    # kimi-k2-thinking-maas (above).
    "kimi-k2.6",
}

# Derived from the central provider registry so adding a new provider in
# metaproc/config/providers.py is a one-place change. The registry already
# folds in pi-cli's built-in cloud-vendor provider names (vertex, azure,
# bedrock) that metaproc does not actively wire.
from metaproc.config.providers import (
    pi_valid_provider_names as _pi_valid_provider_names,  # noqa: E402
)

PI_VALID_PROVIDERS: set[str] = set(_pi_valid_provider_names())

# ── Gemini (continued) ──────────────────────────────────────────

GEMINI_DEFAULT_NATIVE_SETTINGS: dict[str, object] = {
    "agents": {
        "overrides": {
            "generalist": {"enabled": False},
            "codebase_investigator": {"enabled": False},
            "cli_help": {"enabled": False},
        },
    },
    "modelConfigs": {
        "overrides": [
            {
                "match": {"overrideScope": "chat-base-3"},
                "modelConfig": {
                    "generateContentConfig": {
                        "thinkingConfig": {
                            "thinkingLevel": GEMINI_DEFAULT_THINKING_LEVEL,
                        },
                    },
                },
            },
        ],
    },
}
