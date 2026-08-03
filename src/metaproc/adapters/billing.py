"""Cost provenance and credential route for an adapter invocation.

Three independent facts get confused whenever anyone says "what did this cost":

1. **Auth route** — which credential chain the CLI actually used. Sometimes
   provable at launch from the environment and the adapter's own bootstrap.
2. **Cost provenance** — where a dollar figure came from. A provider-returned
   amount, a client-side estimate the CLI computed from a bundled price table,
   or our own pricing-table lookup are three different kinds of number.
3. **Charge status** — whether that amount is actually owed. This is a billing
   outcome, not a property of the run, and is deliberately not recorded.

They are recorded separately because they genuinely vary independently, and
because collapsing them produces confident-looking numbers that are wrong:

- Plan authentication does **not** imply zero marginal cost. Both OpenAI and
  Anthropic sell usage credits that let a plan user continue past the included
  allowance, billed at rate-card/API rates while still authenticating as that
  plan. A `chatgpt_plan` or `claude_plan` route can therefore produce real
  charges.
- An API-key route does **not** imply the local number is the invoice. Claude
  Code's `total_cost_usd` is a client-side estimate from a bundled price table,
  and Anthropic documents that Console/API billing data is authoritative.
- Neither route tells us about promotional credits, contracted rates, or
  committed-spend discounts.

**Metaproc cannot determine charge status from a run artifact**, so it does not
record one. Whether an amount is owed depends on plan allowances, purchased
credits, and contracted rates that no log contains; answering it requires
reconciliation against provider billing, which is a separate layer. Reporting
therefore groups by *cost provenance* and never asserts that an amount is or is
not owed.

Resolution is per-adapter rather than one central table, because each CLI has
its own precedence chain and several supported routes are ambiguous from the
environment alone. Every resolver returns `unknown` rather than guessing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

AuthRoute = Literal[
    "api_key",
    "chatgpt_plan",
    "claude_plan",
    "bedrock",
    "vertex",
    "gateway",
    "unknown",
]
"""Credential chain a CLI authenticated with, when provable."""

CostProvenance = Literal[
    "provider_authoritative",
    "client_list_estimate",
    "pricing_table_estimate",
    "unknown",
]
"""Where a dollar figure came from.

- ``provider_authoritative``: the provider returned this amount for this call.
- ``client_list_estimate``: the CLI computed it locally from a bundled price
  table (Claude Code's ``total_cost_usd``, codex's ``costUSD``). Not an invoice.
- ``pricing_table_estimate``: we computed it from ``pricing.md`` and token
  counts.
- ``unknown``: no basis to characterize it.
"""

UNKNOWN = "unknown"

# Routes whose dollar figures a provider did not hand us. Used only to pick a
# default provenance when the caller does not state one; it says nothing about
# whether money is owed.
_ESTIMATE_ONLY_ROUTES: frozenset[str] = frozenset(
    {"chatgpt_plan", "claude_plan", "bedrock", "vertex", "gateway", "unknown"}
)


def _present(env: dict[str, str] | None, *names: str) -> bool:
    """True when any of *names* holds a usable value in *env*.

    Invocation sidecars redact credential values to a length-only marker, so an
    unset variable and an empty one both have to read as absent: an empty
    ``ANTHROPIC_API_KEY`` does not activate that route in any CLI, and
    ``<redacted len=0>`` records exactly that case. Any other redaction marker
    means a real value was present.
    """
    env = env or {}
    for name in names:
        value = env.get(name)
        if not value:
            continue
        if value.startswith("<redacted len="):
            if value != "<redacted len=0>":
                return True
            continue
        return True
    return False


def _truthy_flag(env: dict[str, str] | None, name: str) -> bool:
    return (env or {}).get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _claude_auth_route(env: dict[str, str] | None) -> AuthRoute:
    """Claude Code's documented precedence, highest first.

    Cloud-provider routing wins over every key, then an explicit auth token
    (typically a gateway), then the API key, then plan OAuth. ``apiKeyHelper``
    is configured in settings rather than the environment, so a run relying on
    it is not distinguishable here and stays ``unknown``.
    """
    if _truthy_flag(env, "CLAUDE_CODE_USE_BEDROCK"):
        return "bedrock"
    if _truthy_flag(env, "CLAUDE_CODE_USE_VERTEX"):
        return "vertex"
    if _present(env, "ANTHROPIC_AUTH_TOKEN"):
        return "gateway"
    if _present(env, "ANTHROPIC_API_KEY"):
        return "api_key"
    if _present(env, "CLAUDE_CODE_OAUTH_TOKEN"):
        return "claude_plan"
    # A stored /login credential (Vehicle B) leaves no environment trace, and
    # apiKeyHelper is a settings-file hook. Both land here.
    return UNKNOWN


def _codex_persisted_auth_mode(env: dict[str, str] | None) -> str | None:
    """``tokens.auth_mode`` from the codex ``auth.json`` this run would read.

    Codex persists API-key auth (``codex login --with-api-key``) as well as
    ChatGPT OAuth, so the environment alone does not settle the route. Pool
    slots pin ``CODEX_HOME``, which is what makes this readable per attempt.
    Best-effort: any read or parse failure yields ``None``.
    """
    home = (env or {}).get("CODEX_HOME") or os.path.expanduser("~/.codex")
    try:
        payload = json.loads((Path(home) / "auth.json").read_text())
    except (OSError, ValueError):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    mode = tokens.get("auth_mode")
    return mode if isinstance(mode, str) else None


def _codex_auth_route(env: dict[str, str] | None) -> AuthRoute:
    """Codex CLI precedence as of the pinned release.

    ``CODEX_API_KEY`` — not ``OPENAI_API_KEY`` — is the per-invocation API-key
    override that ``codex exec`` honors; ``OPENAI_API_KEY`` is what
    ``codex login --with-api-key`` consumes to *create* persisted API-key auth.
    An ambient ``OPENAI_API_KEY`` therefore does not by itself put a run on a
    metered route, so it is not treated as proof of one.

    Persisted ``auth.json`` settles the rest. Pool slots additionally write
    ``forced_login_method = "chatgpt"``, which is why env presence alone was
    never sufficient.
    """
    if _present(env, "CODEX_API_KEY"):
        return "api_key"
    mode = _codex_persisted_auth_mode(env)
    if mode == "apikey":
        return "api_key"
    if mode == "chatgpt":
        return "chatgpt_plan"
    return UNKNOWN


def _gemini_auth_route(env: dict[str, str] | None) -> AuthRoute:
    """Gemini CLI: Vertex/ADC or a direct API key.

    Vertex is metaproc's recommended mode and uses ADC with no key present, so
    absence of a key is emphatically not a plan route.
    """
    if _truthy_flag(env, "GOOGLE_GENAI_USE_VERTEXAI"):
        return "vertex"
    if _present(env, "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        return "api_key"
    return UNKNOWN


def _pi_auth_route(env: dict[str, str] | None) -> AuthRoute:
    """Pi CLI delegates auth to whichever provider its config selects.

    The provider is chosen in pi's own configuration, not the environment, so
    only an unambiguous single-provider key is evidence. Vertex MaaS in
    particular carries no key and is not a metered API-key route.
    """
    if _truthy_flag(env, "GOOGLE_GENAI_USE_VERTEXAI"):
        return "vertex"
    if _present(env, "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        return "api_key"
    return UNKNOWN


_ROUTE_RESOLVERS = {
    "claude-code-cli": _claude_auth_route,
    "codex-cli": _codex_auth_route,
    "gemini-cli": _gemini_auth_route,
    "pi-cli": _pi_auth_route,
}


def resolve_auth_route(adapter_type: str, env: dict[str, str] | None) -> AuthRoute:
    """Credential route for *adapter_type* under *env*, or ``unknown``.

    ``env`` is the environment the adapter subprocess is launched with, or the
    sidecar's recorded snapshot of it. Unregistered adapters yield ``unknown``
    rather than being forced into another adapter's credential model.
    """
    resolver = _ROUTE_RESOLVERS.get(adapter_type)
    if resolver is None:
        return UNKNOWN
    return resolver(env)


def default_cost_provenance(auth_route: str | None, *, self_reported: bool) -> CostProvenance:
    """Provenance for a dollar figure, given how it was obtained.

    A self-reported figure from an agent CLI is a *client-side* estimate: both
    Claude Code and codex compute it locally from a bundled price table rather
    than reading it off a bill. Only a provider that returns a per-call amount
    (Exa does; the agent CLIs do not) is authoritative, and callers with such a
    figure state ``provider_authoritative`` explicitly.
    """
    if self_reported:
        return "client_list_estimate"
    if auth_route in _ESTIMATE_ONLY_ROUTES or auth_route is None:
        return "pricing_table_estimate"
    return "pricing_table_estimate"


def is_authoritative(cost_provenance: str | None) -> bool:
    """True when a figure came from the provider and may be summed as spend."""
    return cost_provenance == "provider_authoritative"


__all__ = [
    "UNKNOWN",
    "AuthRoute",
    "CostProvenance",
    "default_cost_provenance",
    "is_authoritative",
    "resolve_auth_route",
]
