"""Error classification and retry logic for run-parallel.

Classifies subprocess errors as retryable (transient) or permanent based on
pattern matching on the error string from status.yaml. The subprocess is opaque
— exit code is always 1 — so the error string is the only signal.

Modeled after an external tool's errorCategorization.ts but adapted for
metaproc's simpler subprocess context.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from metaproc.io import iter_text_lines, resolve_existing_artifact
from metaproc.models.authored import RetryPolicy
from metaproc.models.runtime import OutputFailure, OutputFailureKind

log = logging.getLogger(__name__)

# ── Verdict ────────────────────────────────────────────────────


class RetryVerdict(StrEnum):
    RETRY = "retry"
    FAIL = "fail"


class FailureClass(StrEnum):
    """Category of failure — surfaces *why* something failed, not just retryability."""

    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    CRASH = "crash"
    UNKNOWN = "unknown"


# ── Error patterns ─────────────────────────────────────────────

# Checked first — if any match, the error is permanent (not retryable).
_PERMANENT_PATTERNS: list[str] = [
    "enospc",
    "enomem",
    "quota",
    "permission denied",
    "billing",
    "credits",
    # Anthropic personal-plan monthly-cap exhaustion (claude-code-cli). Keyed off
    # the verbatim error text from the JSONL `result` field on a 429 response:
    # "You've hit your org's monthly usage limit". Distinct from a 429 burst
    # rate-limit — a monthly cap will not lift on retry timescales.
    "monthly usage limit",
    "monthly limit",
    "out of extra usage",
    "out of usage",
    "exit code 137",  # SIGKILL / OOM — not recoverable by retry
    "exit code 143",  # user-initiated cancellation (SIGTERM) — not retryable
    "cancelled",
    # codex-cli 0.124.0 auth failures in turn.failed.error.message —
    # retrying won't fix a misconfigured OPENAI_API_KEY / stale auth.json.
    "unexpected status 401",
    "missing bearer",
    # Generic client-side 4xx (except 429) surfaces as "unexpected status 4N"
    # in codex — not retryable (retrying won't fix bad input).
    "unexpected status 400",
    "unexpected status 403",
    "unexpected status 404",
]

# Sub-categories within permanent patterns for failure classification.
# The "out of extra usage" / "out of usage" variants come from the
# `result.result` text on Claude Code quota-cap responses — the verbatim
# 2026-05-13 cascade message was "You're out of extra usage · resets 3:10am
# (America/Los_Angeles)". Distinct from the older "monthly usage limit"
# string and from a 429 burst — the cap will not lift until the named reset
# time, so the orchestrator should pause until then rather than retry.
_QUOTA_EXHAUSTED_PATTERNS: list[str] = [
    "monthly usage limit",
    "monthly limit",
    "quota",
    "out of extra usage",
    "out of usage",
]

# Checked second — if any match, the error is transient (retryable).
_TRANSIENT_PATTERNS: list[str] = [
    "timeout",
    "stalled",
    "rate limit",
    "too many requests",
    "429",
    "truncated_headers",
    "gcloud auth",
    "unavailable",
    # Provider-side 5xx family. Anthropic returns
    # `{"type":"error","error":{"type":"api_error","message":"Internal server error"}}`
    # which the orchestrator's error_str sees as
    # `exit code 1 (log: API Error: {...Internal server error...})`. Without
    # these patterns the classifier fell through to bare-exit-code → CRASH,
    # which is operator-confusing — a process didn't crash, the upstream
    # vendor returned a 5xx response.
    "500",
    "internal server error",
    "api_error",
    "503",
    "502",
    "econnrefused",
    "econnreset",
    "connection",
    "log_runaway",
]

# Sub-categories within transient patterns for failure classification.
_RATE_LIMIT_PATTERNS: list[str] = [
    "rate limit",
    "too many requests",
    "429",
]

_TIMEOUT_PATTERNS: list[str] = [
    "timeout",
    "stalled",
    "log_runaway",
]

_SERVER_ERROR_PATTERNS: list[str] = [
    "unavailable",
    "500",
    "internal server error",
    "api_error",
    "503",
    "502",
    "econnrefused",
    "econnreset",
    "connection",
    "truncated_headers",
    "gcloud auth",
]


# ── Classification ─────────────────────────────────────────────


def classify_error(error: str) -> RetryVerdict:
    """Classify an error string as retryable or permanent.

    Priority:
    1. Permanent patterns (blocklist) — checked first
    2. Transient patterns — checked second
    3. Bare "exit code N" — RETRY (most are transient API errors)
    4. "output validation failed" — RETRY (usually transient; exhausts retries if not)
    5. Default — FAIL
    """
    lower = error.lower()

    for pattern in _PERMANENT_PATTERNS:
        if pattern in lower:
            return RetryVerdict.FAIL

    for pattern in _TRANSIENT_PATTERNS:
        if pattern in lower:
            return RetryVerdict.RETRY

    if lower.startswith(("exit code ", "command exit code ")):
        return RetryVerdict.RETRY

    # Output validation failures: retry missing-file cases (transient — the
    # model may have been killed before writing), but treat schema/envelope
    # mismatches as permanent (retrying won't fix a structural mismatch).
    #
    # This substring test reads the artifact's filename along with everything
    # else, so an output named for a schema is judged permanent whatever
    # actually went wrong with it. Prefer ``classify_output_failures``, which
    # answers the same question from the structured record; this path remains
    # for a status record written before those were kept.
    if "output validation failed" in lower:
        if "schema" in lower or "envelope" in lower or "mismatch" in lower:
            return RetryVerdict.FAIL
        return RetryVerdict.RETRY

    return RetryVerdict.FAIL


# Which output failures another attempt could plausibly fix. A file that is not
# there may simply not have been written yet; a document the schema refuses will
# be refused again in exactly the same way.
_RETRYABLE_OUTPUT_FAILURES: frozenset[OutputFailureKind] = frozenset(
    {OutputFailureKind.missing, OutputFailureKind.empty, OutputFailureKind.unreadable}
)


def classify_output_failures(failures: Sequence[OutputFailure]) -> RetryVerdict:
    """Decide whether re-running could fix these output failures.

    The structured form of rule 4 above, and the reason to prefer it: this reads
    what refused the output rather than the sentence describing it, so two
    identical failures cannot receive opposite verdicts because of how their
    artifacts are named.

    Any failure another attempt cannot fix makes the whole set permanent, since
    the step has to produce all of its outputs.
    """
    if not failures:
        return RetryVerdict.FAIL
    if all(f.kind in _RETRYABLE_OUTPUT_FAILURES for f in failures):
        return RetryVerdict.RETRY
    return RetryVerdict.FAIL


def classify_failure(error: str) -> FailureClass:
    """Classify an error string into a failure category.

    Complements ``classify_error()`` — same error string, different lens.
    ``classify_error`` answers "should we retry?"; this answers "why did it fail?"
    """
    lower = error.lower()

    # QUOTA_EXHAUSTED is checked before RATE_LIMITED because quota-exhaustion
    # error strings often co-include "rate" / "429"; the more specific category
    # wins so the operator-facing surface distinguishes "wait until reset" from
    # "wait a few seconds and retry".
    for pattern in _QUOTA_EXHAUSTED_PATTERNS:
        if pattern in lower:
            return FailureClass.QUOTA_EXHAUSTED

    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern in lower:
            return FailureClass.RATE_LIMITED

    for pattern in _TIMEOUT_PATTERNS:
        if pattern in lower:
            return FailureClass.TIMEOUT

    for pattern in _SERVER_ERROR_PATTERNS:
        if pattern in lower:
            return FailureClass.SERVER_ERROR

    if "output validation failed" in lower:
        return FailureClass.INVALID_OUTPUT

    # Bare exit codes or other errors → crash.
    if lower.startswith(("exit code ", "command exit code ")):
        return FailureClass.CRASH

    return FailureClass.UNKNOWN


# Content failures (INVALID_OUTPUT) re-run the same prompt against the same
# inputs; retrying past a small budget rarely produces a different outcome and
# burns the operator's wall clock. Cap content failures separately from the transient-network
# RetryPolicy.max_retries default (12). Override via METAPROC_MAX_CONTENT_RETRIES
# if the operator wants different behaviour for a specific batch.
MAX_CONTENT_FAILURE_RETRIES_DEFAULT = 3


def max_retries_for(failure_class: FailureClass, default_max_retries: int) -> int:
    """Return the per-failure-class retry budget.

    Most failure classes use ``default_max_retries`` (transient-network budget,
    typically 12). ``INVALID_OUTPUT`` is capped at
    :data:`MAX_CONTENT_FAILURE_RETRIES_DEFAULT` because re-running the same
    deterministic prompt rarely changes the outcome.
    """
    if failure_class == FailureClass.INVALID_OUTPUT:
        return min(default_max_retries, MAX_CONTENT_FAILURE_RETRIES_DEFAULT)
    return default_max_retries


# ── Log error extraction ──────────────────────────────────────


def extract_log_error(log_path: Path, tail_lines: int = 30) -> str | None:
    """Extract the terminal error from a subprocess log file.

    Reads the last *tail_lines* of the log and looks for error signals:
    - ``agent_end`` events with an ``errorMessage``
    - Non-JSON lines (raw stderr from the CLI or underlying tools)

    Returns a short error string suitable for appending to the status
    error, or ``None`` if no error signal is found.
    """
    log_path = resolve_existing_artifact(log_path)
    if not log_path.is_file():
        return None
    try:
        lines = [line for _line_no, line in iter_text_lines(log_path)][-tail_lines:]
    except Exception:
        log.debug("Failed to read log tail from %s", log_path, exc_info=True)
        return None

    # Priority 1: structured terminal events.
    #  - Pi CLI: ``agent_end`` with messages[*].errorMessage.
    #  - codex-cli 0.124.0: ``turn.failed`` with error.message (flat envelope).
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        etype = event.get("type")
        if etype == "agent_end":
            messages = event.get("messages", [])
            for msg in reversed(messages):
                err = msg.get("errorMessage")
                if err:
                    return str(err)[:200]
        elif etype == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                msg = error.get("message")
                if msg:
                    return str(msg)[:200]

    # Priority 2: raw stderr lines (non-JSON) — last non-JSON line from the tail
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            if not stripped.startswith("{"):
                return stripped[:200]

    return None


# ── Backoff ────────────────────────────────────────────────────


def compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """Compute backoff delay in seconds for the given retry index (1-indexed).

    Callers pass ``retry_index`` (1 = first retry, 2 = second retry), **not**
    ``attempt_number``. The relation is ``retry_index = attempt_number - 1``.

    Formula: ``initial * multiplier^(retry_index - 1)``, capped at max.

    With ``RetryPolicy`` framework defaults (initial=5s, multiplier=1.5,
    max=600s, max_retries=12):
        1: 5s, 2: 7.5s, 3: 11.25s, 4: 16.9s, 5: 25.3s, 6: 38.0s, 7: 56.9s,
        8: 85.4s, 9: 128.1s, 10: 192.2s, 11: 288.3s, 12: 432.4s
    Total accumulated wait across 12 retries: ~21.5 min. Covers both fast
    429-style recoveries and longer stream-reset / degraded-backend blips
    on the upstream side.
    """
    delay = policy.initial_backoff_s * (policy.backoff_multiplier ** (attempt - 1))
    return min(delay, policy.max_backoff_s)
