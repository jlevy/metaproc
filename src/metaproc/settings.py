"""Adapter and resource defaults.

Process specs select models from the reviewed catalog in
``metaproc.config.model_catalog`` and can override default selections.
"""

from __future__ import annotations

import os as _os

from metaproc.config.model_catalog import MODEL_CATALOG, known_model_ids

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

CLAUDE_DEFAULT_MODEL = MODEL_CATALOG.defaults["claude-code-cli"]
CLAUDE_DEFAULT_EFFORT = "high"

CLAUDE_VALID_MODELS: set[str] = set(known_model_ids("claude-code-cli"))

# ── Gemini ────────────────────────────────────────────────────────

GEMINI_DEFAULT_MODEL = MODEL_CATALOG.defaults["gemini-cli"]
GEMINI_DEFAULT_THINKING_LEVEL = "HIGH"

GEMINI_VALID_MODELS: set[str] = set(known_model_ids("gemini-cli"))

# ── Codex ────────────────────────────────────────────────────────
#
# codex-cli (OpenAI Codex CLI, npm @openai/codex) defaults. The adapter
# dispatches through `codex exec --json`; see
# src/metaproc/docs/metaproc-design.md
# for design context. Model review evidence lives in config/model_catalog.py.

CODEX_DEFAULT_MODEL = MODEL_CATALOG.defaults["codex-cli"]
CODEX_DEFAULT_EFFORT = "medium"

CODEX_VALID_MODELS: set[str] = set(known_model_ids("codex-cli"))

# `codex exec --sandbox <MODE>` — the three upstream-documented modes.
CODEX_VALID_SANDBOX_MODES: set[str] = {
    "read-only",
    "workspace-write",
    "danger-full-access",
}

# `codex -a <POLICY>` — top-level flag. The pinned CLI parses only `on-request`
# and `never`; `on-failure` (removed in 0.143.0) and `untrusted` (removed in
# 0.149.0) stay accepted in process files and are translated to `on-request`
# by the adapter (CODEX_LEGACY_APPROVAL_POLICIES), since `codex exec` forces
# `never` either way.
CODEX_VALID_APPROVAL_POLICIES: set[str] = {
    "untrusted",
    "on-failure",
    "on-request",
    "never",
}

# `-c model_reasoning_effort=<EFFORT>` — codex-cli config-path override.
# Named reasoning levels in the pinned 0.166.0 parser. Availability is model-specific;
# accepting the syntax does not mean every model supports every effort.
CODEX_VALID_EFFORTS: set[str] = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
}

# ── Pi ───────────────────────────────────────────────────────────

PI_DEFAULT_MODEL = MODEL_CATALOG.defaults["pi-cli"]
PI_DEFAULT_PROVIDER = "anthropic"

PI_VALID_MODELS: set[str] = set(known_model_ids("pi-cli"))

# Derived from the central provider registry so adding a new provider in
# metaproc/config/providers.py is a one-place change. The registry already
# folds in pi-cli's built-in cloud-vendor provider names (vertex, azure,
# bedrock) that metaproc does not actively wire.
from metaproc.config.providers import (
    pi_valid_provider_names as _pi_valid_provider_names,  # noqa: E402
)

PI_VALID_PROVIDERS: set[str] = set(_pi_valid_provider_names())

# ── Gemini (continued) ──────────────────────────────────────────

# Gemini CLI runs session-retention cleanup at startup as an un-awaited
# background task. Before it knows which sessions are expired, cleanup
# enumerates the project's `chats/` directory and parses every saved session
# JSONL through an unbounded `Promise.all`. On an accumulated project bucket
# that turns a ~0.4 GB process tree into 5+ GB for the same short prompt, which
# is why the key below is a host-safety setting and not a preference.
#
# Controlled arms (gemini-cli 0.55.1, same prompt, model, repo, and host; only
# the project-state regime varied): 0.39 GB clean, 5.07 GB with a copied
# 3.4 GiB bucket, 0.40 GB with that same bucket and retention disabled.
#
# `cleanupExpiredSessions()` early-exits on a falsy
# `general.sessionRetention.enabled` *before* enumerating, so this setting
# prevents the scan rather than merely bounding it. Verified in 0.55.1
# (upstream 41327e407) and again in the pinned 0.66.0, whose
# `getAllSessionFiles` still loads every session file through one unbounded
# `Promise.all` -- upgrading does not fix it.
#
# Tradeoff: the same switch also gates `cleanupToolOutputFiles`, so Gemini no
# longer prunes its own `chats/` and `tool-outputs/`. Bounded retention becomes
# an external operation.
#
# See docs/project/research/research-2026-09-01-gemini-cli-project-state-memory.md
GEMINI_SESSION_RETENTION_SETTINGS: dict[str, object] = {
    "general": {
        "sessionRetention": {
            "enabled": False,
        },
    },
}

# Metaproc keeps gemini-cli on its static model-resolution path. Through 0.60 that
# path answered any id ending in "flash" with the CLI's own flash default, which is
# why Metaproc once turned `experimental.dynamicModelConfiguration` on. 0.61.0
# replaced that test with an exact list, and in the pinned 0.66.0 the static path
# sends every concrete catalog id unchanged. The dynamic path now does the opposite
# of what it was enabled for: its `modelIdResolutions` table rewrites an explicit
# gemini-3.1-pro-preview to gemini-3.1-pro-preview-customtools under API-key auth
# (observed live on 2026-11-18), and it judges preview status and multimodal tool
# output from model definitions that 3.6 Flash and Gemini 4 do not have. The flag
# does not change thought-signature handling, which tests the id's name on both
# paths; Gemini 4 tool calls round-tripped with either setting. Both paths still
# send gemini-3.5-flash and gemini-3-flash as gemini-3.8-flash, and
# gemini-3.1-flash-lite as gemini-3.5-flash-lite, under Vertex, API-key and
# gateway auth; the catalog no longer accepts those ids. The key is asserted
# false, not left out, so a working directory's settings cannot switch the
# rewriting path back on unnoticed.
GEMINI_DYNAMIC_MODEL_SETTINGS: dict[str, object] = {
    "experimental": {
        "dynamicModelConfiguration": False,
    },
}

# Model-config aliases gemini-cli 0.66.0 ships that extend `chat-base-3`, which sets
# `thinkingLevel: HIGH`. A chat model without its own alias falls back to
# `chat-base`, which sets no thinking level, so the model runs at its server
# default: medium for 3.6 Flash, Gemini 4 Argon and Gemini 4 Carbon.
GEMINI_CLI_CHAT_BASE_3_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
    }
)

# Concrete catalog ids that need their own thinking-level override. Matching an
# id with `match: {model: <id>}` is safe only because no internal gemini-cli
# alias (classifier, loop detection, compression, web fetch) uses one of these
# ids as its base model; those utility configs set their own thinking budgets.
GEMINI_UNALIASED_CHAT_MODELS: tuple[str, ...] = tuple(
    sorted(
        model
        for model in GEMINI_VALID_MODELS
        if model.startswith("gemini-") and model not in GEMINI_CLI_CHAT_BASE_3_MODELS
    )
)


def _gemini_thinking_override(match: dict[str, str]) -> dict[str, object]:
    return {
        "match": match,
        "modelConfig": {
            "generateContentConfig": {
                "thinkingConfig": {
                    "thinkingLevel": GEMINI_DEFAULT_THINKING_LEVEL,
                },
            },
        },
    }


# gemini-cli matches an override's `model` key against the requested model and
# every alias in its inheritance chain, so `{model: "chat-base-3"}` reaches every
# chat model the CLI configures and `{model: <id>}` reaches one un-aliased model.
# `overrideScope` names a sub-agent (codebase_investigator, generalist), not an
# alias: the `{overrideScope: "chat-base-3"}` match Metaproc wrote before 0.66.0
# never applied, and HIGH held only where the CLI's own alias already set it.
GEMINI_THINKING_OVERRIDES: list[dict[str, object]] = [
    _gemini_thinking_override({"model": "chat-base-3"}),
    *(_gemini_thinking_override({"model": model}) for model in GEMINI_UNALIASED_CHAT_MODELS),
]

GEMINI_DEFAULT_NATIVE_SETTINGS: dict[str, object] = {
    "experimental": {
        "dynamicModelConfiguration": False,
    },
    "general": {
        "sessionRetention": {
            "enabled": False,
        },
    },
    "context": {
        "fileFiltering": {
            "respectGitIgnore": False,
        },
    },
    "agents": {
        "overrides": {
            "generalist": {"enabled": False},
            "codebase_investigator": {"enabled": False},
            "cli_help": {"enabled": False},
        },
    },
    "modelConfigs": {
        "overrides": GEMINI_THINKING_OVERRIDES,
    },
}

# The defaults Metaproc wrote to a working directory's `.gemini/settings.json` under
# `native_settings_scope: workspace` before it recorded them beside that file: v0.5.0
# through v0.6.1 all wrote this object. A file without that record whose values differ
# from today's defaults only where it holds one of these is a file Metaproc wrote, and
# the adapter replaces those keys with a warning instead of refusing the relaunch.
# Later releases record their own defaults, so a default change, including one the
# model catalog drives through GEMINI_UNALIASED_CHAT_MODELS, needs no entry here.
GEMINI_UNRECORDED_DEFAULT_NATIVE_SETTINGS: tuple[dict[str, object], ...] = (
    {
        "experimental": {"dynamicModelConfiguration": True},
        "general": {"sessionRetention": {"enabled": False}},
        "context": {"fileFiltering": {"respectGitIgnore": False}},
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
                        "generateContentConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}},
                    },
                },
            ],
        },
    },
)
