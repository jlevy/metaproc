"""Billing class for an adapter invocation — metered vs subscription.

Agent CLIs bill through two incompatible vehicles, and conflating them
produces a cost number that means nothing:

- **metered**: an API key bills per token or per request. The dollar figure is
  money owed, and it scales with the workload.
- **subscription**: a personal plan (Claude Code Personal Plan, ChatGPT-plan
  Codex) bills a flat periodic fee. The marginal cost of one more request is
  approximately zero, so *the meaningful unit is tokens, not dollars*. Any
  dollar figure is a **list-price equivalent** — what the same tokens would
  have cost through the metered vehicle — and must never be summed with real
  metered spend.

Which vehicle is in play is decided by credential precedence, documented in
``docs/runbooks/credential-setup.runbook.md``. Both Claude Code and codex
silently prefer an API key over subscription credentials when both are
present, so a stray inherited key flips an invocation from flat-rate to
metered without any other visible change. That is exactly the transition this
module makes legible: resolution is by the same precedence the CLIs use, from
the environment the process is actually launched with.
"""

from __future__ import annotations

from typing import Literal

BillingClass = Literal["metered", "subscription", "unknown"]

METERED: BillingClass = "metered"
SUBSCRIPTION: BillingClass = "subscription"
UNKNOWN: BillingClass = "unknown"

# Env var whose presence forces an adapter onto its pay-per-token vehicle,
# overriding any subscription credential. Mirrors each CLI's own precedence
# chain; see the credential runbook's "Vehicle" sections.
_METERED_KEY_BY_ADAPTER: dict[str, tuple[str, ...]] = {
    "claude-code-cli": ("ANTHROPIC_API_KEY",),
    "codex-cli": ("OPENAI_API_KEY",),
    "gemini-cli": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

# Adapters with no subscription vehicle at all — always pay-per-token.
# pi-cli authenticates with provider API keys for every provider it supports.
_ALWAYS_METERED: frozenset[str] = frozenset({"pi-cli"})


def resolve_billing_class(adapter_type: str, env: dict[str, str] | None) -> BillingClass:
    """Billing vehicle for *adapter_type* under *env*.

    ``env`` is the environment the adapter subprocess is launched with (or the
    recorded snapshot of it). A key present but empty does not count — the CLIs
    treat an empty value as unset.

    Returns ``unknown`` for adapters this module has no rule for, so an
    unregistered adapter degrades to "do not claim to know" rather than
    silently reporting subscription tokens as metered dollars.
    """
    if adapter_type in _ALWAYS_METERED:
        return METERED
    keys = _METERED_KEY_BY_ADAPTER.get(adapter_type)
    if keys is None:
        return UNKNOWN
    env = env or {}
    if any(env.get(k) for k in keys):
        return METERED
    return SUBSCRIPTION


def is_subscription(billing_class: str | None) -> bool:
    """True when a dollar figure for this class is a list-price equivalent."""
    return billing_class == SUBSCRIPTION


__all__ = [
    "METERED",
    "SUBSCRIPTION",
    "UNKNOWN",
    "BillingClass",
    "is_subscription",
    "resolve_billing_class",
]
