"""Known-bug signature registry (plan-2026-04-21 P5.3).

A small add-only registry of failure patterns that represent
*software bugs*, not credential or quota problems. When a dispatch
error matches one of these patterns the classifier returns a
:class:`~metaproc.adapters.base.AuthFailureClassification` with
``severity=ABORT`` and ``known_bug_signature`` set — so operators
see the bug immediately rather than burning retries on it and the
pool doesn't flip a healthy credential to cooling/expired because
of a software defect on our side.

**Add-only by design.** Bugs don't disappear from the registry once
a fix ships — the pattern stays so we can aggregate "how often are
we re-detecting a supposedly-fixed bug?" from logs over time. That
empirical signal is worth more than the minor noise of carrying a
resolved pattern forever.

**Matching targets:**

- ``stderr`` — the subprocess's stderr text (or the extracted error
  string from ``status.yaml``).
- ``session_log`` — the per-step CLI session log JSONL content,
  when the caller can afford to read it. Pattern matches against
  the raw log text (not parsed events).

**Never scans the blob.** Matching happens on operator-facing error
text only; OAuth payloads don't reach this module.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)


MatchTarget = Literal["stderr", "session_log"]


@dataclass(frozen=True)
class KnownBugSignature:
    """One entry in the :data:`KNOWN_BUG_REGISTRY`.

    ``name`` is the operator-facing identifier — the string that
    ends up in ``known_bug_signature`` and shows up in `metaproc
    auth status` output + `auth_outcome` events. Keep it short,
    kebab-case, and stable: operators grep log aggregates by it.

    ``pattern`` is a compiled regex. Patterns are
    matched as ``re.search`` (not full-match) so they can target a
    phrase inside a larger error message.

    ``target`` names which text stream the pattern runs against.

    ``hint`` is a short sentence pointing the operator at what to
    do — the fix, a ticket link, or a diagnostic command. Never
    printed verbatim in machine output; only in the text renderer.

    ``first_seen`` is a dated comment documenting when the pattern
    first fired in prod. Strictly informational, but surfaces in
    the spec-facing audit table when a signature has been detected
    after its purported fix date.
    """

    name: str
    pattern: re.Pattern[str]
    target: MatchTarget
    hint: str
    first_seen: str = ""


# ── Registry ────────────────────────────────────────────────────

# Add new signatures at the bottom. Patterns are add-only; do not
# remove one that ever fired in prod — we want to observe
# reoccurrence.
KNOWN_BUG_REGISTRY: list[KnownBugSignature] = [
    # The 2026-04-23 pattern: after a rate-limit wave, Claude Code
    # CLI exits with code 1 in 15-45s with no useful stderr. Pairs
    # with the startup-failure circuit breaker; the circuit handles
    # the pool-level recovery while this signature gives operators
    # a grep-able name for log aggregation.
    #
    # The pattern has to be narrow enough that it fires ONLY on the
    # genuinely silent case. Real credential failures (invalid_grant,
    # authentication_error, unauthorized) and diagnostic words
    # (error/exception/traceback/panic/…) all indicate the CLI
    # reported a cause — those belong to the terminal-credential
    # path or the normal retry classifier, not here. The lookahead
    # chain excludes both HTTP-status shapes and informative prose.
    KnownBugSignature(
        name="claude-startup-exit-1-silent",
        pattern=re.compile(
            r"exit code 1\b"
            r"(?!.*anthropic-ratelimit)"
            r"(?!.*\d{3,})"
            r"(?!.*invalid_grant)"
            r"(?!.*authentication_error)"
            r"(?!.*unauthorized)"
            r"(?!.*billing_error)"
            r"(?!.*traceback)"
            r"(?!.*exception)"
            r"(?!.*\berror\b)"
            r"(?!.*panic)",
            re.IGNORECASE | re.DOTALL,
        ),
        target="stderr",
        hint=(
            "Claude Code CLI exited 1 without a clear reason. Commonly "
            "a fresh rate-limit-rejection before the session log had "
            "time to emit a rate_limit_event. StartupFailureCircuit "
            "should have tripped after 3 consecutive — if it didn't, "
            "check the circuit breaker's window_s setting."
        ),
        first_seen="2026-04-23",
    ),
    # plan-2026-04-23-dep-role-simplification retired the packet.yaml
    # subsystem. Old processes that haven't been re-scaffolded
    # FileNotFoundError on packet.yaml paths. The fix is to
    # regenerate process scaffolds; the detector ensures we spot
    # stale callers rather than wasting retries.
    KnownBugSignature(
        name="retired-packet-yaml-path",
        pattern=re.compile(r"FileNotFoundError[^\n]*packet\.yaml", re.IGNORECASE),
        target="stderr",
        hint=(
            "packet.yaml was retired in plan-2026-04-23-dep-role-"
            "simplification. Regenerate this process's scaffold via "
            "`metaproc plan` so the inline output contracts replace "
            "the packet reference."
        ),
        first_seen="2026-04-23",
    ),
    # MCP schema-config validation failures — the CLI rejects the
    # adapter config before any model work happens; retrying won't
    # fix a malformed config.
    KnownBugSignature(
        name="mcp-schema-config-invalid",
        pattern=re.compile(
            r"strict.?mcp.?config.*(invalid|malformed|validation)",
            re.IGNORECASE,
        ),
        target="stderr",
        hint=(
            "MCP config validation failed. Check the adapter_config "
            "dict in the step spec; strict_mcp_config=true requires "
            "every MCP server to validate against its declared "
            "schema before the CLI starts."
        ),
        first_seen="2026-04-20",
    ),
    # Version-mismatch tracebacks — metaproc wheel and the worker's
    # metaproc install drifted. Retries can't fix an install skew.
    # Matches either order:
    # - `ImportError: cannot import name 'X' from 'metaproc.Y'`
    #   (worker missing a symbol the dispatcher expects)
    # - `AttributeError: module 'metaproc.X' has no attribute 'Y'`
    #   (stale wheel — symbol renamed/removed since worker was built)
    KnownBugSignature(
        name="metaproc-version-mismatch",
        pattern=re.compile(
            r"(ImportError[^\n]*cannot import[^\n]*metaproc\.)"
            r"|(AttributeError[^\n]*metaproc\.[^\n]*no attribute)",
            re.IGNORECASE,
        ),
        target="stderr",
        hint=(
            "Worker's metaproc module is older than the dispatcher. "
            "Rebuild the wheel (`uv build --wheel`), re-upload to "
            "METAPROC_WHEEL_GCS, and redeploy workers before retrying."
        ),
        first_seen="2026-04-21",
    ),
]


def detect_known_bug(
    *,
    stderr: str = "",
    session_log_text: str = "",
) -> KnownBugSignature | None:
    """Return the first :class:`KnownBugSignature` that matches.

    Patterns are tested in registry order. Returns ``None`` when
    nothing matches — the classifier then falls back to the
    adapter's normal cooling/expired/unknown decision.

    Classifiers pass in whatever they already have cheaply at hand.
    Neither argument is required: if stderr is available and none
    of the stderr-target signatures match, we don't even need to
    read the session log. This keeps the hot path cheap.
    """
    for sig in KNOWN_BUG_REGISTRY:
        text = stderr if sig.target == "stderr" else session_log_text
        if not text:
            continue
        if sig.pattern.search(text):
            log.info(
                "known-bug detector fired: %s (target=%s)",
                sig.name,
                sig.target,
            )
            return sig
    return None


def registered_signatures() -> list[str]:
    """Return the names of every registered signature.

    Used by the `metaproc auth status --verbose` output and by
    operator tooling that wants to audit the registry without
    exposing the underlying regex patterns.
    """
    return [sig.name for sig in KNOWN_BUG_REGISTRY]


__all__ = [
    "KNOWN_BUG_REGISTRY",
    "KnownBugSignature",
    "MatchTarget",
    "detect_known_bug",
    "registered_signatures",
]
