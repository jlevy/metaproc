"""Claude Code CLI adapter."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from metaproc.adapters.base import (
    AuthFailureClassification,
    AuthStatus,
    ConfigRejection,
    FailureSeverity,
    QuotaUsage,
    parse_jsonl_event,
    resolve_templates,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_usage import query_label_headroom
from metaproc.dispatch.credential_pool import Vehicle
from metaproc.dispatch.known_bugs import detect_known_bug
from metaproc.settings import (
    CLAUDE_DEFAULT_EFFORT,
    CLAUDE_DEFAULT_MODEL,
    CLAUDE_VALID_MODELS,
)

# Environment variable carrying the Keychain credential payload to Batch
# workers. Retained for compatibility with the legacy credential path.
CLAUDE_CREDS_ENV_VAR = "CLAUDE_CODE_CREDS_JSON"

# macOS Keychain service name that `claude login` writes to. Used by
# capture_credential to source blobs for `metaproc auth push`.
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"

# Pinned Claude Code CLI version. To update it:
#   1. update this constant
#   2. install the matching version locally (`claude` self-updates; verify
#      `claude --version` matches)
#   3. rebuild any deployment images and run an adapter smoke test
PINNED_CLAUDE_CODE_CLI_VERSION = "2.1.207"
CLAUDE_CODE_INSTALL_HINT = (
    f"Install: npm install -g @anthropic-ai/claude-code@{PINNED_CLAUDE_CODE_CLI_VERSION}"
)

log = logging.getLogger(__name__)


class ClaudeCodeVersionMismatch(RuntimeError):
    """Raised when the on-PATH ``claude`` binary's version does not match
    :data:`PINNED_CLAUDE_CODE_CLI_VERSION`.

    Drift between local and cloud `claude` versions has caused real
    incidents (the 2026-05-03 cloud-tool-surface investigation). The
    pin + this check make drift fail loudly instead of silently.
    """


def verify_claude_version(
    *, claude_path: str = "claude", expected: str = PINNED_CLAUDE_CODE_CLI_VERSION
) -> str:
    """Verify the on-PATH ``claude`` binary matches the pinned version.

    Runs ``claude --version`` (which prints e.g. ``2.1.126 (Claude Code)``),
    parses the leading version token, compares to *expected*. Returns the
    parsed version string on success. Raises :class:`ClaudeCodeVersionMismatch`
    on mismatch or when the binary is missing / unparsable.

    Result is intentionally NOT cached here — callers that want
    process-lifetime caching can wrap with :func:`functools.lru_cache`.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — claude_path is operator-controlled
            [claude_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        msg = (
            f"Could not run `{claude_path} --version`: {exc}. "
            f"Expected pinned version {expected!r}; install or fix PATH."
        )
        raise ClaudeCodeVersionMismatch(msg) from exc
    if proc.returncode != 0:
        msg = (
            f"`{claude_path} --version` exited {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()!r}. "
            f"Expected pinned version {expected!r}."
        )
        raise ClaudeCodeVersionMismatch(msg)
    output = proc.stdout.strip() or proc.stderr.strip()
    actual = output.split()[0] if output else ""
    if actual != expected:
        msg = (
            f"Claude Code CLI version mismatch: pinned={expected!r}, "
            f"actual={actual!r} (`{claude_path} --version` -> {output!r}). "
            "Update PINNED_CLAUDE_CODE_CLI_VERSION and any deployment-image "
            "pins together."
        )
        raise ClaudeCodeVersionMismatch(msg)
    return actual


@functools.cache
def _ensure_claude_version_pinned() -> str:
    """Cache the pinned-version check to one shell-out per process.

    Wraps :func:`verify_claude_version` in :func:`functools.cache`. Adapters
    call this on the hot path (every ``build_command``); without caching we
    would shell out per-item.

    Set ``METAPROC_SKIP_CLAUDE_VERSION_CHECK=1`` to bypass — useful in test
    environments (CI doesn't install the claude binary) and for local dev
    against a non-pinned version. Returns the pinned version string in that
    case so callers see a stable value.
    """
    skip = MetaprocEnv.METAPROC_SKIP_CLAUDE_VERSION_CHECK.read_str(default="").lower()
    if skip in ("1", "true", "yes"):
        return PINNED_CLAUDE_CODE_CLI_VERSION
    return verify_claude_version()


@functools.cache
def _claude_version_drift() -> str | None:
    """Return a drift message if the on-PATH ``claude`` mismatches the pin, else
    None. Non-blocking: version drift is surfaced as a prominent warning, not a
    hard error. Honors ``METAPROC_SKIP_CLAUDE_VERSION_CHECK``.
    """
    skip = MetaprocEnv.METAPROC_SKIP_CLAUDE_VERSION_CHECK.read_str(default="").lower()
    if skip in ("1", "true", "yes"):
        return None
    try:
        verify_claude_version()
    except ClaudeCodeVersionMismatch as exc:
        log.warning("harness CLI version drift — %s", exc)
        return str(exc)
    return None


def _validate_claude_creds_blob(blob: str) -> None:
    """Raise if *blob* is not a JSON object with a top-level ``claudeAiOauth`` key."""
    try:
        parsed: Any = json.loads(blob)
    except json.JSONDecodeError as exc:
        msg = f"Claude credentials blob is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(parsed, dict) or "claudeAiOauth" not in cast("dict[str, Any]", parsed):
        msg = (
            "Claude credentials blob is missing required top-level "
            "key 'claudeAiOauth'; refusing to write a malformed credentials file"
        )
        raise RuntimeError(msg)


def _fingerprint_blob(blob: str) -> str:
    """12-char hex SHA-256 prefix used for fp labels and rotation detection."""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# Pacific wall-clock phrase in Claude error bodies, e.g. "resets 11am
# (America/Los_Angeles)". Fallback parser when the session log does not
# carry a structured rate_limit_event with resetsAt. Intentionally
# conservative: matches hour:minute with optional am/pm and the PT zone.
_RESETS_PHRASE_RE = re.compile(
    r"resets\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?\s*"
    r"\(America/Los_Angeles\)",
    re.IGNORECASE,
)

_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def _parse_pacific_reset_phrase(match: re.Match[str], *, now: datetime | None = None) -> int | None:
    """Convert an ``_RESETS_PHRASE_RE`` match into an epoch ``cooling_until_ts``.

    Interprets the wall-clock time in ``America/Los_Angeles`` and returns
    the UTC epoch seconds for the next occurrence. If the named time is
    in the past for today in Pacific wall-clock, rolls forward to
    tomorrow. Returns ``None`` if the hour fails sanity checks (so the
    caller still marks the label cooling but the selection logic knows
    the timestamp is soft).

    Accepts 12-hour form with am/pm (``11am``, ``3:30pm``) and bare
    24-hour form (``18`` → ``18:00``). Minute defaults to ``0`` when
    the group is absent. AM/PM without a meridiem group is ambiguous;
    we assume the next upcoming occurrence in the 24-hour interpretation.
    """
    hour_str = match.group("hour")
    minute_str = match.group("minute")
    meridiem = match.group("meridiem")
    try:
        hour = int(hour_str)
    except (TypeError, ValueError):
        return None
    minute = int(minute_str) if minute_str else 0

    if meridiem:
        meridiem_l = meridiem.lower()
        if meridiem_l == "am":
            if hour == 12:
                hour = 0
            elif not 1 <= hour <= 12:
                return None
        elif meridiem_l == "pm":
            if hour == 12:
                pass
            elif 1 <= hour <= 11:
                hour += 12
            else:
                return None
    elif not 0 <= hour <= 23:
        return None

    if not 0 <= minute <= 59:
        return None

    current = now if now is not None else datetime.now(tz=_PACIFIC_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_PACIFIC_TZ)
    else:
        current = current.astimezone(_PACIFIC_TZ)

    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate = candidate + timedelta(days=1)
    return int(candidate.timestamp())


_CLAUDE_ALLOWED_KEYS = frozenset(
    {
        "append_system_prompt",
        "cache",
        "effort",
        "estimated_process_rss_bytes",
        "estimated_process_rss_mb",
        "host_max_concurrency",
        "initial_memory_budget_fraction",
        "max_budget_usd",
        "model",
        "native_settings",
        "no_session_persistence",
        "output_format",
        "permission_mode",
        "strict_mcp_config",
        "timeout_s",
        "tools",
        "verbose",
        "worktree",
    }
)


# When the classifier maps a failure to expired / known-bug, the
# ``reason`` field carries an excerpt of the actual underlying error
# so operators don't have to grep stream-json or claude-code-debug.log to
# answer "why did label X fail?".
_ERROR_EXCERPT_MAX = 200


# Patterns shared between this module's classify_failure and
# `metaproc auth probe` so the HTTP-signal extraction lives in one
# place as Claude Code's debug format evolves.
_OAUTH_REFRESH_RE = re.compile(
    r"AxiosError:\s*\[url=https?://[^/]+/v\d+/oauth/token,\s*status=(\d+)\]"
)
_API_ERROR_STATUS_RE = re.compile(r"\[ERROR\]\s+API error\s*\(attempt\s+\d+/\d+\):\s*(\d{3})")
_API_ERROR_STATUS_FALLBACK_RE = re.compile(r'"api_error_status"\s*:\s*(\d+)')
_REQUEST_ID_RE = re.compile(r'"request_id":\s*"([^"]+)"')
_CLIENT_REQUEST_ID_RE = re.compile(r"x-client-request-id=([0-9a-f-]+)")
_RETRY_AFTER_RE = re.compile(r'"retry_after"\s*:\s*(\d+)|retry-after[:=]\s*(\d+)', re.IGNORECASE)


@dataclass(frozen=True)
class ClaudeApiSignals:
    """Structured HTTP-axis signals parsed from a ``claude -d api`` run.

    Populated by :func:`parse_claude_api_signals` from any combination of
    stderr, the stream-json session log, and the slot-local
    ``claude-code-debug.log`` file. Empty / ``None`` fields mean "signal not
    found in the input"; an all-empty result is normal for a successful
    run that produced no error output.
    """

    api_status: int | None = None
    oauth_refresh_status: int | None = None
    request_id: str = ""
    error_body_excerpt: str = ""
    retry_after_s: int | None = None


def parse_claude_api_signals(*, debug_text: str = "", stdout: str = "") -> ClaudeApiSignals:
    """Pull HTTP-axis signals from ``claude -d api`` debug log + stdout.

    Inputs are typically:
    - ``debug_text`` — content of ``<slot>/claude-code-debug.log`` written by
      ``claude -d api --debug-file=...``.
    - ``stdout`` — claude's user-visible output, which carries the final
      ``Failed to authenticate. API Error: 401 ...`` body with
      ``request_id`` baked in.

    Either input may be empty; the function searches whichever is
    available. Returns a :class:`ClaudeApiSignals` whose fields default
    to ``None`` / ``""`` when no signal matches.

    Patterns recognized today:

    - ``AxiosError: [url=https://platform.claude.com/v1/oauth/token, status=N]``
      → ``oauth_refresh_status``
    - ``[ERROR] API error (attempt N/M): <status>`` → ``api_status``
    - ``"api_error_status": N`` (stream-json result event) →
      ``api_status`` fallback
    - ``"request_id": "<id>"`` or ``x-client-request-id=<uuid>`` →
      ``request_id``
    - ``"retry_after": N`` or ``retry-after: N`` → ``retry_after_s``
    """
    oauth_status: int | None = None
    if (m := _OAUTH_REFRESH_RE.search(debug_text)) is not None:
        try:
            oauth_status = int(m.group(1))
        except (TypeError, ValueError):
            oauth_status = None

    # Stream-json structured pass: parse each result event as JSON.
    # Inner result strings carry JSON-escaped quotes so the regex below
    # would miss request_id / retry_after embedded inside them; the
    # JSON parse unescapes them once, then the regex picks up bare
    # quotes in the unescaped body.
    structured_result_bodies: list[str] = []
    structured_api_status: int | None = None
    for line in stdout.splitlines():
        ev = parse_jsonl_event(line, "result")
        if ev is None:
            continue
        api_err = ev.get("api_error_status")
        if isinstance(api_err, int) and structured_api_status is None:
            structured_api_status = api_err
        result_body = ev.get("result")
        if isinstance(result_body, str) and result_body:
            structured_result_bodies.append(result_body)

    api_status: int | None = None
    for m in _API_ERROR_STATUS_RE.finditer(debug_text):
        try:
            api_status = int(m.group(1))
        except (TypeError, ValueError):
            continue
    if api_status is None:
        api_status = structured_api_status
    if api_status is None:
        for source in (stdout, debug_text):
            if (m := _API_ERROR_STATUS_FALLBACK_RE.search(source)) is not None:
                try:
                    api_status = int(m.group(1))
                    break
                except (TypeError, ValueError):
                    continue

    request_id = ""
    # Prefer request_id from the unescaped stream-json result body —
    # that's the canonical user-visible Anthropic request_id.
    for body in structured_result_bodies:
        if (m := _REQUEST_ID_RE.search(body)) is not None:
            request_id = m.group(1)
            break
    if not request_id:
        if (
            (m := _REQUEST_ID_RE.search(stdout)) is not None
            or (m := _REQUEST_ID_RE.search(debug_text)) is not None
            or (m := _CLIENT_REQUEST_ID_RE.search(debug_text)) is not None
        ):
            request_id = m.group(1)

    error_body_excerpt = ""
    # Prefer the unescaped result body for the operator-readable
    # excerpt (no backslash-quote noise).
    for body in structured_result_bodies:
        stripped = body.strip().splitlines()[0] if body.strip() else ""
        if "API Error" in stripped or "authentication_error" in stripped:
            error_body_excerpt = stripped[:_ERROR_EXCERPT_MAX]
            break
    if not error_body_excerpt:
        for line in stdout.splitlines():
            stripped = line.strip()
            if "API Error" in stripped or "authentication_error" in stripped:
                error_body_excerpt = stripped[:_ERROR_EXCERPT_MAX]
                break
    if not error_body_excerpt:
        for line in debug_text.splitlines():
            stripped = line.strip()
            if "[ERROR] API error" in stripped or "AxiosError" in stripped:
                error_body_excerpt = stripped[:_ERROR_EXCERPT_MAX]
                break

    retry_after_s: int | None = None
    # Try the unescaped result bodies first.
    for body in structured_result_bodies:
        if (m := _RETRY_AFTER_RE.search(body)) is not None:
            for group in m.groups():
                if group:
                    try:
                        retry_after_s = int(group)
                        break
                    except (TypeError, ValueError):
                        continue
            if retry_after_s is not None:
                break
    if retry_after_s is None:
        combined = f"{stdout}\n{debug_text}"
        if (m := _RETRY_AFTER_RE.search(combined)) is not None:
            for group in m.groups():
                if group:
                    try:
                        retry_after_s = int(group)
                        break
                    except (TypeError, ValueError):
                        continue

    return ClaudeApiSignals(
        api_status=api_status,
        oauth_refresh_status=oauth_status,
        request_id=request_id,
        error_body_excerpt=error_body_excerpt,
        retry_after_s=retry_after_s,
    )


def _extract_error_excerpt(stderr: str, session_log_text: str) -> str:
    """Pull the most informative error string from stderr + session log.

    Priority (most → least specific):
    1. Stream-json result event with ``is_error`` / ``api_error_status``
       (``Failed to authenticate. API Error: 401 ...``).
    2. ``[ERROR] API error (attempt N/M):`` line from ``claude -d api``
       output (when ``classify_failure_for_slot`` prepended the
       slot-local ``claude-code-debug.log``).
    3. ``AxiosError: [url=https://platform.claude.com/v1/oauth/token,
       status=400]`` — OAuth refresh failure boundary.
    4. First non-empty stderr line as a last resort.

    Returns the slice trimmed to ``_ERROR_EXCERPT_MAX`` chars; never the
    full payload (no leaked tokens; the result body never carries one).
    """
    # 1. Look for a stream-json result event with is_error.
    for line in session_log_text.splitlines():
        if '"is_error":true' in line or '"is_error": true' in line:
            ev = parse_jsonl_event(line, "result")
            if ev is None:
                continue
            result = ev.get("result")
            if isinstance(result, str) and result:
                return result[:_ERROR_EXCERPT_MAX]
    # 2. Per-attempt API error lines from claude -d api debug output.
    for source in (stderr, session_log_text):
        for line in source.splitlines():
            stripped = line.strip()
            if "[ERROR] API error" in stripped or "API error (attempt" in stripped:
                return stripped[:_ERROR_EXCERPT_MAX]
    # 3. OAuth refresh AxiosError.
    for source in (stderr, session_log_text):
        for line in source.splitlines():
            stripped = line.strip()
            if "AxiosError" in stripped and "/oauth/token" in stripped:
                return stripped[:_ERROR_EXCERPT_MAX]
    # 4. Fallback: first non-empty stderr line.
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_ERROR_EXCERPT_MAX]
    return ""


def _build_claude_flags(
    merged_config: dict[str, object],
    variables: dict[str, str],
) -> list[str]:
    """Build CLI flags for ``claude -p`` from merged config."""
    flags: list[str] = []

    model = merged_config.get("model") or CLAUDE_DEFAULT_MODEL
    model_str = str(model)
    if model_str in CLAUDE_VALID_MODELS:
        flags.extend(["--model", model_str])
    else:
        log.warning(
            "claude-code-cli: ignoring unknown model name %r; falling back to default %r",
            model_str,
            CLAUDE_DEFAULT_MODEL,
        )
        flags.extend(["--model", CLAUDE_DEFAULT_MODEL])

    effort = merged_config.get("effort") or CLAUDE_DEFAULT_EFFORT
    flags.extend(["--effort", str(effort)])

    permission_mode = merged_config.get("permission_mode")
    if not permission_mode:
        msg = (
            "claude-code-cli: 'permission_mode' is required in adapter config. "
            "Without it, claude will prompt for permission on every tool call "
            "and fail silently in batch mode (stdin is /dev/null). "
            "Set permission_mode: bypassPermissions in the adapter config."
        )
        raise ValueError(msg)
    flags.extend(["--permission-mode", str(permission_mode)])
    # Claude Code 2.1.123+ — bypassPermissions alone is insufficient when
    # CLAUDE_CONFIG_DIR is scoped to a per-attempt slot dir. The session's
    # default "allowed working directories" is the slot dir, so Write tool
    # calls to paths *outside* the slot (e.g. the process-item output_root
    # which lives under the run dir but in a different subtree from the
    # slot) get blocked. Two fixes pair:
    #   1. --dangerously-skip-permissions disables the permission gate.
    #   2. --add-dir <cwd> grants tool access to the entire project tree
    #      (cwd is the project root for orchestrator-spawned subprocesses).
    if str(permission_mode) == "bypassPermissions":
        # See metaproc/docs/arch/arch-claude-code-harness.md for the full design
        # (env / settings / permissions surface, ENV_SCRUB interaction, why
        # the wide --allowedTools list is the right tradeoff, and why
        # --setting-sources=user isolates the slot from project settings).
        flags.append("--dangerously-skip-permissions")
        flags.extend(["--add-dir", str(Path.cwd().resolve())])
        flags.extend(
            [
                "--allowedTools",
                "Bash Write Edit Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit",
            ]
        )
        flags.extend(["--setting-sources", "user"])

    tools = merged_config.get("tools")
    if isinstance(tools, list):
        flags.extend(["--tools", ",".join(str(t) for t in cast("list[Any]", tools))])

    if merged_config.get("strict_mcp_config") is True:
        flags.append("--strict-mcp-config")

    max_budget = merged_config.get("max_budget_usd")
    if max_budget:
        flags.extend(["--max-budget-usd", str(max_budget)])

    output_format = str(merged_config.get("output_format") or "stream-json")
    flags.extend(["--output-format", output_format])

    if merged_config.get("verbose") is not False:
        flags.append("--verbose")

    if merged_config.get("no_session_persistence") is not False:
        flags.append("--no-session-persistence")

    append_system_prompt = merged_config.get("append_system_prompt")
    if append_system_prompt:
        resolved_sys = resolve_templates(str(append_system_prompt), variables)
        flags.extend(["--append-system-prompt", resolved_sys.strip()])

    if merged_config.get("worktree"):
        flags.append("--worktree")

    return flags


class ClaudeCodeCliAdapter:
    """Adapter for Claude Code CLI (``claude``)."""

    adapter_type: str = "claude-code-cli"
    short_name: str = "claude-cli"
    default_model: str | None = CLAUDE_DEFAULT_MODEL

    # ── AuthCapableCliAdapter subprotocol (plan-2026-04-21-auth-credential-pool.md) ──
    slot_credential_filename: str = ".credentials.json"
    # Cross-provider fallback is opt-in only per spec OQ9. Claude-Codex
    # compatibility is semantic (prompt/tool behavior), not credential-blob;
    # operators declare compatibility explicitly per-adapter later.
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def preflight(self) -> str | None:
        # Plan-time prerequisite check: surface any `claude` version drift at
        # launch (as a prominent warning, not a block) rather than when the
        # first claude step executes mid-DAG.
        return _claude_version_drift()

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        # Surface any pinned-version drift as a warning (cached, once per
        # process); non-blocking so the run proceeds even on version skew.
        _claude_version_drift()
        flags = _build_claude_flags(merged_config, variables)
        return ["claude", "-p", f"@{prompt_file}", *flags]

    def validate_config(self, merged_config: dict[str, object]) -> list[ConfigRejection]:
        rejections: list[ConfigRejection] = []
        for key, value in merged_config.items():
            if key not in _CLAUDE_ALLOWED_KEYS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"{key!r} is not supported by claude-code-cli")
                )
                continue
            if key == "model" and str(value) not in CLAUDE_VALID_MODELS:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=f"unknown claude-code-cli model {value!r}",
                    )
                )
        return rejections

    def prepare_env(
        self,
        env: dict[str, str],
        merged_config: dict[str, object],
    ) -> dict[str, str]:
        # Scrub Claude Code-internal entry-point markers. When metaproc is
        # itself driven by a Claude Code session (the dispatch operator's
        # IDE), the parent session's CLAUDECODE / CLAUDE_CODE_ENTRYPOINT
        # env vars get inherited through the orchestrator -> worker dispatch
        # chain and the dispatched ``claude --print`` subprocess re-detects
        # itself as "local-agent" or "remote", loading the orchestration
        # tool surface (Monitor / PushNotification / RemoteTrigger) instead
        # of the standard execution surface (Bash / Read / Write / Edit /
        # Grep / Glob). Result: process-item produces template-shell
        # output because Claude cannot run the configured wrapper via Bash. Drop
        # both vars before invoking claude.
        env = {k: v for k, v in env.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        # IS_SANDBOX=1 bypasses Claude Code's root-user refusal for
        # --dangerously-skip-permissions / permission_mode bypassPermissions.
        # Cloud workers run as uid 0, and the pre-built agent image USED to
        # set this globally — but that also activated Claude Code's internal
        # network sandbox proxy on every claude invocation, including the
        # pre-flight probe that doesn't pass --dangerously-skip-permissions
        # (and the proxy's allowedDomains doesn't reliably pass through
        # claude-code#30112 / #37970). Set IS_SANDBOX=1 only when this
        # specific launch will pass --dangerously-skip-permissions (i.e.
        # permission_mode is bypassPermissions); the probe path uses
        # credential_scope_env (not prepare_env) and laptop/local launches
        # without bypass don't need the root-user override.
        if merged_config.get("permission_mode") == "bypassPermissions":
            env["IS_SANDBOX"] = "1"
        native_settings = merged_config.get("native_settings")
        if isinstance(native_settings, dict):
            log.warning(
                "claude-code-cli: native_settings is not yet supported "
                "(no injection mechanism confirmed); ignoring: %s",
                list(cast("dict[str, Any]", native_settings).keys()),
            )
        return env

    def working_directory(self, _merged_config: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        return parse_jsonl_event(line, "result")

    def check_auth(self) -> AuthStatus:
        cli_path = shutil.which("claude")
        api_key = MetaprocEnv.ANTHROPIC_API_KEY.read_str(default=None)

        if not cli_path:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=False,
                cli_path=None,
                credentials_found=False,
                auth_mode="none",
                details="claude CLI not found on PATH",
                setup_hint=CLAUDE_CODE_INSTALL_HINT,
            )

        if api_key:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="api-key",
                details="ANTHROPIC_API_KEY is set",
                setup_hint="",
            )

        scoped_config = MetaprocEnv.CLAUDE_CONFIG_DIR.read_str(default="")
        claude_config = Path(scoped_config) if scoped_config else Path.home() / ".claude"
        has_session = claude_config.exists()

        return AuthStatus(
            adapter_type=self.adapter_type,
            cli_found=True,
            cli_path=cli_path,
            credentials_found=has_session,
            auth_mode="interactive-login" if has_session else "none",
            details=(
                f"Using interactive login session ({claude_config})"
                if has_session
                else "No ANTHROPIC_API_KEY and no interactive login session found"
            ),
            setup_hint=(
                ""
                if has_session
                else "Option 1: export ANTHROPIC_API_KEY=sk-ant-...\nOption 2: claude login"
            ),
        )

    def auth_info(self) -> str:
        return (
            "Claude Code CLI auth modes:\n"
            "\n"
            "  1. API key (recommended for batch runs):\n"
            "     export ANTHROPIC_API_KEY=sk-ant-...\n"
            "\n"
            "  2. Interactive login (personal plan):\n"
            "     claude login\n"
        )

    def bootstrap(self, home: Path) -> None:
        # Materialize the Personal-Plan OAuth credential from the
        # CLAUDE_CODE_CREDS_JSON env var (set by the Batch Secret Manager
        # binding) to ~/.claude/.credentials.json, then unset the env
        # var so the payload does not leak to child process environments.
        # No-op when the env var is absent — laptop/local-dev paths are
        # unaffected and rely on the user's Keychain-managed session.
        # Also no-op under the per-slot pool path (METAPROC_AUTH_POOL_RUN=1),
        # which materializes into the slot dir, not the container home.
        if MetaprocEnv.METAPROC_AUTH_POOL_RUN.read_str(default="") == "1":
            # Pool path owns credential materialization — do not contend
            # with the slot coordinator by writing into ~/.claude here.
            return
        creds_json = os.environ.pop(CLAUDE_CREDS_ENV_VAR, "")
        if not creds_json:
            return

        _validate_claude_creds_blob(creds_json)

        claude_dir = home / ".claude"
        claude_dir.mkdir(mode=0o700, exist_ok=True)
        claude_dir.chmod(0o700)
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(creds_json)
        creds_file.chmod(0o600)
        log.info("claude-code auth: materialized ~/.claude/.credentials.json")

    # ── AuthCapableCliAdapter methods ───────────────────────────────

    def debug_capture_args(self, slot_dir: Path) -> list[str]:
        """Capture Claude Code's API-level debug output into the slot.

        ``claude -d api`` emits the OAuth refresh attempt boundaries
        (``[API:auth] OAuth token check ...``), the refresh-endpoint
        AxiosError on failure, and per-attempt ``[API REQUEST] /v1/messages``
        + ``[ERROR] API error`` lines. Without this, the only failure
        signature available to the classifier is the user-visible
        ``Failed to authenticate. API Error: 401 ...`` body, which
        cannot distinguish OAuth-refresh-failure from access-token-rejection.

        The filename is reported by :meth:`diagnostic_filenames` so the
        orchestrator preserves it next to the session log on teardown.
        """
        return ["-d", "api", "--debug-file", str(slot_dir / "claude-code-debug.log")]

    def diagnostic_filenames(self) -> tuple[str, ...]:
        return ("claude-code-debug.log",)

    def setup_token_command(self) -> list[str] | None:
        """Return argv for ``claude setup-token``.

        Claude Code's documented one-time interactive flow that mints
        a long-lived (~1 year) ``CLAUDE_CODE_OAUTH_TOKEN`` for headless
        use. ``metaproc auth setup`` spawns this attached to the
        operator's terminal — operator follows the OAuth flow in their
        browser, completion writes the token to stdout (which metaproc
        captures), prompts and any errors flow through stderr.
        """
        return ["claude", "setup-token"]

    def credential_scope_env(
        self,
        slot_dir: Path,
        *,
        vehicle: Any = None,
        blob: str = "",
    ) -> dict[str, str]:
        """Scope Claude Code CLI credential lookup to *slot_dir*.

        Vehicle B (default / vehicle is None or LOGIN_CREDENTIALS):
        scopes ``CLAUDE_CONFIG_DIR`` only. The CLI reads
        ``<slot>/.credentials.json`` (materialized via
        :meth:`materialize_credential`).

        Vehicle A (OAUTH_TOKEN — schema_version 2): also injects
        ``CLAUDE_CODE_OAUTH_TOKEN=<blob>`` plus ``DISABLE_UPDATES=1``.
        The static-bearer token is the credential; no
        ``.credentials.json`` is written. ``DISABLE_UPDATES`` stops the
        CLI from auto-updating mid-cohort, which would mix unverified
        versions into a running dispatch.

        ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`` is conditionally set:
        - Local (laptop, multi-process host): set, because the
          subprocess-scrub + bubblewrap PID-namespace isolation
          prevents auth-env-var leak into Bash subprocesses spawned
          by tools / hooks / MCP children sharing the host.
        - Cloud worker (GCP Batch, single-purpose VM, running as root):
          NOT set. Claude Code 2.1.x's env-scrub feature requires the
          subprocess to run as a non-root user and forces
          ``permission_mode=default`` + restricts the tool set to
          harness-internal tools (Monitor / PushNotification /
          RemoteTrigger) when those preconditions don't hold. Since
          the GCP Batch VM is single-purpose and already isolated at
          the VM boundary, the nested env-scrub adds no real
          protection but breaks the dispatch — claude rejects every
          Bash/Read/Write/Edit tool call as "not in allowed list" and
          the run halts. Detected by reading ``METAPROC_GCP_PROJECT``
          (set by worker_dispatch.py); on the laptop the var is
          present too, but ``METAPROC_WORKER_ID`` only exists inside
          a Batch worker container.
        """
        env = {"CLAUDE_CONFIG_DIR": str(slot_dir)}
        if vehicle is Vehicle.OAUTH_TOKEN:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = blob
            env["DISABLE_UPDATES"] = "1"
            in_cloud_worker = bool(MetaprocEnv.METAPROC_WORKER_ID.read_str(default=""))
            if not in_cloud_worker:
                env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
        return env

    def credential_scrub_env(self, *, vehicle: Any = None) -> dict[str, str]:
        """Unset higher-precedence Claude auth vars before spawning a slot.

        Documented precedence chain (current Anthropic docs, 2026-04-28):
        cloud-provider → ``ANTHROPIC_AUTH_TOKEN`` → ``ANTHROPIC_API_KEY``
        → ``apiKeyHelper`` → ``CLAUDE_CODE_OAUTH_TOKEN`` → stored
        ``/login`` credentials. A pooled credential is useless if any
        higher-precedence var is live in the subprocess env — the step
        would appear to use the pool while actually hitting a different
        account. ``ANTHROPIC_API_KEY`` is deliberately NOT scrubbed:
        enterprise API-key mode is an explicit operator choice, so
        instead the coordinator refuses slot acquisition when it sees
        ``ANTHROPIC_API_KEY`` live, with an explicit warning.

        Vehicle B (default): scrubs ``CLAUDE_CODE_OAUTH_TOKEN`` along
        with the other higher-precedence vars (the credential is the
        ``.credentials.json`` blob, not the env var).

        Vehicle A (OAUTH_TOKEN): omits ``CLAUDE_CODE_OAUTH_TOKEN`` from
        the scrub set — that env var IS the credential under Vehicle A,
        set by :meth:`credential_scope_env`. Still scrubs every other
        higher-precedence var (cloud-provider mode flags,
        ``ANTHROPIC_AUTH_TOKEN``, ``apiKeyHelper`` helper, the
        provisioning-only ``CLAUDE_CODE_OAUTH_REFRESH_TOKEN`` /
        ``CLAUDE_CODE_OAUTH_SCOPES`` vars).
        """
        scrub: dict[str, str] = {
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_APIKEY_HELPER": "",
            # Cloud-provider mode flags would silently route every API
            # call away from the pooled subscription credential.
            "CLAUDE_CODE_USE_BEDROCK": "",
            "CLAUDE_CODE_USE_VERTEX": "",
            "CLAUDE_CODE_USE_FOUNDRY": "",
            # Provisioning surface for `claude auth login` — distinct from
            # the long-lived setup-token (Vehicle A) credential. Must
            # never be inherited from the operator's shell.
            "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_SCOPES": "",
        }
        if vehicle is not Vehicle.OAUTH_TOKEN:
            # Vehicle B path: the env var would silently win over the
            # slot's .credentials.json if leaked from the operator's
            # shell. Vehicle A path skips this scrub because the env
            # var IS the credential.
            scrub["CLAUDE_CODE_OAUTH_TOKEN"] = ""
        return scrub

    def materialize_credential(
        self,
        slot_dir: Path,
        blob: str,
        *,
        vehicle: Any = None,
    ) -> None:
        """Materialize the credential into the slot directory.

        Vehicle B (default): writes ``<slot_dir>/.credentials.json``
        mode 0600 with the OAuth blob.

        Vehicle A (OAUTH_TOKEN): does NOT write ``.credentials.json``.
        The static-bearer token is injected via
        :meth:`credential_scope_env` instead. This method still creates
        ``slot_dir`` mode 0700 and asserts that no
        ``.credentials.json`` or ``settings.json`` exists in the slot —
        defense in depth against a leaked settings file with
        ``apiKeyHelper``, which would silently override Vehicle A in
        the precedence chain.
        """
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        slot_dir.chmod(0o700)
        # Pre-write hardening so claude --print actually reaches the API:
        #
        # 1. ``.claude.json`` with ``hasCompletedOnboarding: true`` —
        #    anthropics/claude-code#46259 makes the CLI exit 1 silently
        #    if the onboarding gate isn't satisfied, regardless of
        #    CLAUDE_CODE_OAUTH_TOKEN.
        # 2. ``settings.json`` with ``sandbox.enabled: false`` — the
        #    Dockerfile sets IS_SANDBOX=1 to bypass the root-user check
        #    on --dangerously-skip-permissions, but that env var also
        #    activates Claude Code's network sandbox proxy. Without an
        #    explicit allowedDomains list, every outbound request
        #    (including Claude's own api.anthropic.com calls) returns
        #    403 with X-Proxy-Error: blocked-by-allowlist. The container
        #    is already isolated by the GCP Batch VM boundary, so the
        #    nested sandbox is redundant.
        #
        # Both files have to live in the slot dir because per-item
        # launches set CLAUDE_CONFIG_DIR=<slot_dir>; the Dockerfile
        # bakes parallel files at /root/.claude{,.json} for non-slot
        # paths (orchestrator pre-flight, ad-hoc invocations).
        #
        # Reference: https://code.claude.com/docs/en/sandboxing
        onboarding_marker = slot_dir / ".claude.json"
        onboarding_marker.write_text('{"hasCompletedOnboarding": true}\n')
        onboarding_marker.chmod(0o600)
        # Sandbox allowlist (must mirror Dockerfile.agent's bake). The probe
        # path no longer activates the sandbox (IS_SANDBOX moved out of
        # global env into per-invocation prepare_env), so the probe doesn't
        # need an allowlist. Per-slot fan-out launches DO get IS_SANDBOX=1
        # via prepare_env and need the allowlist to reach Anthropic, so we
        # still bake a settings.json into the slot. (Known caveat: claude
        # may not honor allowedDomains reliably — claude-code#30112 /
        # #37970 — in which case fan-out process-item calls will need
        # operator follow-up. The probe path is unaffected.)
        sandbox_settings = slot_dir / "settings.json"
        sandbox_settings.write_text(
            '{"sandbox": {"network": {"allowedDomains": ['
            '"api.anthropic.com", "*.anthropic.com", '
            '"claude.ai", "*.claude.ai", '
            '"code.claude.com", '
            '"registry.npmjs.org", '
            '"*.googleapis.com", '
            '"github.com", "*.github.com", '
            '"raw.githubusercontent.com"'
            "]}}}\n"
        )
        sandbox_settings.chmod(0o600)
        if vehicle is Vehicle.OAUTH_TOKEN:
            # Verify nothing in the slot would silently out-vote the
            # static token. The slot is fresh per dispatch, so this
            # post-condition check should always pass for credential
            # files. settings.json is written above with a known-safe
            # shape (sandbox allowlist, no apiKeyHelper), so any stray
            # pre-existing settings.json is harmlessly overwritten —
            # we only refuse on stray .credentials.json, which is the
            # one slot file the materialize path never writes.
            for name in (self.slot_credential_filename,):
                stray = slot_dir / name
                if stray.exists():
                    msg = (
                        f"Vehicle A slot {slot_dir} contains stray {name!r}; "
                        "refusing to materialize. The token-only path requires "
                        "an empty CLAUDE_CONFIG_DIR — a stray .credentials.json "
                        "would silently override CLAUDE_CODE_OAUTH_TOKEN."
                    )
                    raise RuntimeError(msg)
            # Defense in depth: if a future change ever causes
            # settings.json to land here with apiKeyHelper, fail loud.
            settings_text = sandbox_settings.read_text()
            if "apiKeyHelper" in settings_text:
                msg = (
                    f"Vehicle A slot {slot_dir} settings.json contains "
                    "apiKeyHelper; refusing to materialize. The token-only "
                    "path forbids any helper that would override "
                    "CLAUDE_CODE_OAUTH_TOKEN."
                )
                raise RuntimeError(msg)
            return
        # Vehicle B (legacy default): write the snapshot blob.
        _validate_claude_creds_blob(blob)
        creds_file = slot_dir / self.slot_credential_filename
        creds_file.write_text(blob)
        creds_file.chmod(0o600)

    def capture_credential(self) -> str:
        """Read the live Claude credential from the macOS Keychain.

        Used only by ``metaproc auth push``. The operator must have run
        ``claude login`` on this Mac first. Raises ``RuntimeError`` with
        an actionable message if the platform is not macOS or the
        Keychain item is missing; the caller surfaces the message to
        the operator without mutating any pool state.
        """
        if sys.platform != "darwin":
            msg = (
                "Claude credential capture requires macOS: the Personal-Plan "
                "credential is held in the login Keychain. Run this on the "
                "developer laptop that executed `claude login`."
            )
            raise RuntimeError(msg)
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    CLAUDE_KEYCHAIN_SERVICE,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            msg = (
                f"Failed to read Keychain item {CLAUDE_KEYCHAIN_SERVICE!r}: "
                f"{stderr or exc}. Has `claude login` been run on this Mac?"
            )
            raise RuntimeError(msg) from exc
        # `security -w` appends a trailing newline; strip so the
        # captured blob matches the round-trip the worker will see.
        blob = result.stdout.rstrip("\n")
        _validate_claude_creds_blob(blob)
        return blob

    def classify_failure(
        self,
        exc: BaseException | None,
        stderr: str,
        session_log_path: Path | None,
    ) -> AuthFailureClassification:
        """Classify a Claude Code CLI failure for pool state transitions.

        Priority order:
        1. Terminal credential signals in stderr (``invalid_grant``,
           ``authentication_error``) → expired / ABORT. Runs first so
           a broad known-bug signature can't mask a dead refresh token
           and leave the bad credential active in the pool.
        2. Known-bug signature (plan §Phase 5 P5.3) → severity=ABORT.
           Bugs always abort; we don't retry past a known bug.
        3. Structured ``rate_limit_event`` from session log (has
           ``resetsAt`` — precise ``cooling_until_ts``).
        4. Stderr containing ``too_many_requests`` or
           ``overloaded_error`` → cooling without a timestamp.
        5. Pacific wall-clock phrase in the error body → cooling with
           a best-effort parsed timestamp (noon-of-today or tomorrow).
        6. Anything else → ``unknown`` (generic retry classifier wins).

        Each return path populates ``severity`` explicitly per the
        three-way taxonomy (RETRY_NOW / RETRY_AFTER_WAIT / ABORT).
        Never returns the payload. ``exc`` is unused today but kept
        in the signature so future callers can pass an exception
        carrying structured HTTP response metadata.
        """

        del exc  # reserved for future structured exception hand-off

        stderr_lc = stderr.lower()

        # Read session log up front so the priority-1 auth-error check
        # below can search both stderr and the stream-json result event.
        # Stream-json failures put ``authentication_error`` in the result
        # event, not stderr; checking only stderr misses 401 cascades.
        # Pool dispatch's classify_failure_for_slot prepends slot-local
        # ``claude-code-debug.log`` content to ``stderr`` so OAuth refresh
        # AxiosError lines are visible here too.
        session_log_text = ""
        if session_log_path is not None and session_log_path.exists():
            try:
                session_log_text = session_log_path.read_text()
            except OSError:
                session_log_text = ""

        excerpt = _extract_error_excerpt(stderr, session_log_text)

        # Parse HTTP signals once so every return path threads them
        # through ``http_kwargs`` consistently.
        signals = parse_claude_api_signals(debug_text=stderr, stdout=session_log_text)
        http_kwargs: dict[str, Any] = {
            "api_status": signals.api_status,
            "oauth_refresh_status": signals.oauth_refresh_status,
            "request_id": signals.request_id,
            "error_body_excerpt": signals.error_body_excerpt,
            "retry_after_s": signals.retry_after_s,
        }

        # 1. Terminal auth signals → expired (ABORT). Detection requires
        # a *structured* signal (HTTP status from the parser), not just a
        # keyword anywhere in the session log — agents emit
        # ``authentication_error`` / ``invalid_grant`` in tool output and
        # model text routinely, and a substring match would falsely mark
        # the credential expired and cost the cohort items per fan-out.
        oauth_refresh_failed = (
            signals.oauth_refresh_status is not None and signals.oauth_refresh_status >= 400
        )
        api_auth_rejected = signals.api_status in (401, 403)
        if oauth_refresh_failed or api_auth_rejected:
            if oauth_refresh_failed:
                reason_prefix = f"oauth_refresh_status={signals.oauth_refresh_status}"
            else:
                reason_prefix = f"api_status={signals.api_status}"
            reason = reason_prefix
            if excerpt:
                reason = f"{reason} | {excerpt}"
            # Both refresh-race and access-token-rejected are recoverable
            # via failover to the alt label: ``RETRY_AFTER_WAIT`` adds
            # the failed label to ``pool_exclude`` and the next acquire
            # picks the alt (10.2). ``mark_expired`` from a sibling
            # teardown also marks the failed label ineligible for new
            # acquisitions in parallel. The earlier ``ABORT`` for
            # api-401-only was over-conservative: it assumed retry meant
            # same-label retry, but the orchestrator's retry path always
            # excludes the failed label first. Keeping ABORT was the
            # reason 52/52 production+production items burned with retry_count=0
            # despite alt2 being eligible — fixed here.
            return AuthFailureClassification(
                status="expired",
                reason=reason,
                severity=FailureSeverity.RETRY_AFTER_WAIT,
                **http_kwargs,
            )

        # 2. Known-bug signatures short-circuit the rest of the quota
        # classifier: a software bug is ABORT regardless of what
        # cooling/expired heuristics would say.
        bug = detect_known_bug(stderr=stderr, session_log_text=session_log_text)
        if bug is not None:
            reason = f"known-bug:{bug.name}"
            if excerpt:
                reason = f"{reason} | {excerpt}"
            return AuthFailureClassification(
                status="unknown",  # not a credential issue
                reason=reason,
                severity=FailureSeverity.ABORT,
                known_bug_signature=bug.name,
                **http_kwargs,
            )

        # 3. Session log rate_limit_event with resetsAt.
        if session_log_text:
            try:
                latest_blocked: dict[str, Any] | None = None
                for line in session_log_text.splitlines():
                    ev = parse_jsonl_event(line, "rate_limit_event")
                    if ev is None:
                        continue
                    info_raw: Any = ev.get("rate_limit_info", {})
                    if not isinstance(info_raw, dict):
                        continue
                    info = cast("dict[str, Any]", info_raw)
                    if info.get("status") == "blocked":
                        latest_blocked = info
                if latest_blocked is not None:
                    resets_at_raw: Any = latest_blocked.get("resetsAt")
                    cooling_until: int | None = None
                    if isinstance(resets_at_raw, (int, float)):
                        cooling_until = int(resets_at_raw)
                    limit_type = str(latest_blocked.get("rateLimitType", "unknown"))
                    return AuthFailureClassification(
                        status="cooling",
                        cooling_until_ts=cooling_until,
                        reason=f"rate_limit_event:{limit_type}",
                        severity=FailureSeverity.RETRY_AFTER_WAIT,
                        **http_kwargs,
                    )
            except OSError:
                # Log fragile; keep classifying — stderr path below.
                pass

        # 4. Quota-family soft signals → cooling without ts (RETRY_AFTER_WAIT).
        if "too_many_requests" in stderr_lc or "overloaded_error" in stderr_lc:
            return AuthFailureClassification(
                status="cooling",
                cooling_until_ts=None,
                reason="too_many_requests/overloaded",
                severity=FailureSeverity.RETRY_AFTER_WAIT,
                **http_kwargs,
            )

        # 5. Pacific wall-clock phrase fallback. Parse the matched
        # hour/minute into the next Pacific reset epoch so selection
        # can auto-recover once the reset passes. If the hour is
        # unparsable we still mark the label cooling but leave the
        # timestamp None so the pool treats it as soft — otherwise the
        # label would be stranded "cooling indefinitely" exactly when
        # the error body told us a concrete reset time.
        match = _RESETS_PHRASE_RE.search(stderr)
        if match:
            cooling_until_ts = _parse_pacific_reset_phrase(match)
            return AuthFailureClassification(
                status="cooling",
                cooling_until_ts=cooling_until_ts,
                reason=f"reset-phrase:{match.group(0)}",
                severity=FailureSeverity.RETRY_AFTER_WAIT,
                **http_kwargs,
            )

        # 6. Generic transient signatures (5xx, conn reset, timeout)
        # surface here via the engine retry classifier — we return
        # ``unknown`` so the pool doesn't mark the credential cooling,
        # but we flag RETRY_NOW severity so the dispatch can retry
        # immediately without waiting. The engine-level classifier
        # owns the actual retry decision.
        if any(
            token in stderr_lc
            for token in ("503", "502", "econnrefused", "econnreset", "timeout", "stalled")
        ):
            return AuthFailureClassification(
                status="unknown",
                reason="transient-network-or-5xx",
                severity=FailureSeverity.RETRY_NOW,
                **http_kwargs,
            )

        # 7. Default — truly unknown. Severity defaults to RETRY_NOW
        # via effective_severity() so dispatch doesn't torch the
        # credential for a genuinely unclassified failure.
        return AuthFailureClassification(status="unknown", reason="", **http_kwargs)

    def flush_refreshed_credential(self, slot_dir: Path) -> str | None:
        """Return the current slot blob; caller fingerprints vs bootstrap.

        macOS divergence trap: Claude Code on macOS may write rotated
        OAuth tokens to the OS Keychain rather than the slot's
        ``.credentials.json``, even with ``CLAUDE_CONFIG_DIR`` set
        (anthropic/claude-code#19456). When that happens this method
        returns the bootstrap blob unchanged, the pool never observes
        the rotation, and the stored refresh token drifts stale. See
        ``docs/arch/arch-authentication.md`` §N.11 for the full failure
        mode + mitigations (pre-flight probe gate, refresh-race
        retry-via-failover). Phase 10's centralized OAuth refresh
        sidesteps this entirely.
        """
        creds_file = slot_dir / self.slot_credential_filename
        if not creds_file.exists():
            return None
        try:
            return creds_file.read_text()
        except OSError:
            return None

    def query_quota_usage(self, slot_dir: Path) -> QuotaUsage | None:
        """Synthesize a :class:`QuotaUsage` from recent rate_limit_events.

        Anthropic doesn't publish a native per-account quota endpoint
        for Claude Code, so this adapter derives utilization from the
        most-recent ``rate_limit_event`` records found across recent
        runs under ``$RUNS_DIR``, attributed to this label via the
        Phase 1 ``auth_lease_acquired`` join keys.

        ``slot_dir`` is the sentinel path
        :func:`metaproc.dispatch.preflight.summarize_headroom` passes
        — its final component encodes the label name (the one piece
        of context this adapter needs without spinning up a real slot).

        Returns ``None`` when:
        - ``RUNS_DIR`` is unset (no place to look for evidence);
        - no recent rate_limit_event exists for the label.

        ``None`` propagates up to ``summarize_headroom`` as "unknown",
        which then falls back to the per-adapter constant in
        ``CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM`` — same fail-safe
        as before this method shipped.

        ``remaining_ratio`` is ``1.0 - max(5h_util, 7d_util)`` —
        whichever window is closer to its cap binds first. ``unit_kind``
        is ``"window-utilization"`` to make the derivation explicit
        in any rendered output.
        """

        runs_dir_raw = MetaprocEnv.RUNS_DIR.read_str(default="")
        if not runs_dir_raw:
            return None
        label = slot_dir.name
        if not label:
            return None
        five_hour, seven_day = query_label_headroom("claude-code-cli", label, Path(runs_dir_raw))
        utils: list[float] = []
        if five_hour is not None and five_hour.utilization is not None:
            utils.append(float(five_hour.utilization))
        if seven_day is not None and seven_day.utilization is not None:
            utils.append(float(seven_day.utilization))
        if not utils:
            return None
        max_util = max(utils)
        remaining_ratio = max(0.0, 1.0 - max_util)
        # Earliest reset (whichever window resets sooner) so the
        # caller can plan around the binding constraint.
        resets_at = None
        for window in (five_hour, seven_day):
            if window is None or window.resets_at is None:
                continue
            if resets_at is None or window.resets_at < resets_at:
                resets_at = window.resets_at
        binding = "5h" if five_hour and five_hour.utilization == max_util else "7d"
        return QuotaUsage(
            remaining_ratio=remaining_ratio,
            remaining_units=None,
            total_units=None,
            resets_at=resets_at,
            unit_kind="window-utilization",
            detail=f"binding={binding}; util={max_util:.2f} (1.0 = exhausted)",
        )

    def query_live_quota(
        self,
        slot_dir: Path,
        *,
        vehicle: Any = None,
        blob: str = "",
    ) -> QuotaUsage | None:
        """Hit Anthropic's per-account quota HUD endpoint (live).

        Calls ``GET https://api.anthropic.com/api/oauth/usage`` with the
        slot's OAuth access token, or the Vehicle A static bearer token,
        plus ``anthropic-beta: oauth-2025-04-20``.
        Returns a :class:`QuotaUsage` summarizing the more-binding of the
        five-hour and seven-day windows.

        Unlike :meth:`query_quota_usage`, this works before the first
        429 of a run — it asks the vendor directly instead of deriving
        from rate-limit-event records. The endpoint is the same one
        Claude's own desktop HUD calls.

        Errors (missing creds, network failure, 401, schema drift) all
        return ``None`` so the CLI surface can render ``—`` rather than
        blocking on a best-effort signal.
        """
        access_token = (
            blob.strip()
            if vehicle is Vehicle.OAUTH_TOKEN and blob.strip()
            else _extract_oauth_access_token(slot_dir / self.slot_credential_filename)
        )
        if not access_token:
            return None
        return _query_anthropic_oauth_usage(access_token)


_ANTHROPIC_OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_ANTHROPIC_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_LIVE_QUOTA_TIMEOUT_S = 10.0


def _extract_oauth_access_token(creds_path: Path) -> str | None:
    """Read ``claudeAiOauth.accessToken`` from a slot's ``.credentials.json``.

    Returns ``None`` when the file is absent, unparsable, or doesn't
    carry the expected ``claudeAiOauth`` envelope. Never returns or
    logs the token itself.
    """
    if not creds_path.is_file():
        return None
    try:
        raw = json.loads(creds_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if isinstance(token, str) and token:
        return token
    return None


def _query_anthropic_oauth_usage(access_token: str) -> QuotaUsage | None:
    """Hit ``/api/oauth/usage`` and normalize the response.

    The endpoint's utilization scale isn't guaranteed: community examples
    show both ``0.0-1.0`` ratios and ``0-100`` percents. The parser
    treats anything > 1.0 as a percent.
    """

    req = urllib.request.Request(  # noqa: S310 — fixed HTTPS URL
        _ANTHROPIC_OAUTH_USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": _ANTHROPIC_OAUTH_BETA_HEADER,
            "User-Agent": "metaproc-auth-quota/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_LIVE_QUOTA_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return _quota_unavailable_from_http_error(exc)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    except Exception:  # noqa: BLE001 — quota probe must never raise
        return None
    return _normalize_oauth_usage(payload)


def _quota_unavailable_from_http_error(exc: Any) -> QuotaUsage | None:
    """Return an operator-facing unavailable reason for known HTTP failures."""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        body = ""
    body_lc = body.lower()
    if exc.code == 403 and "user:profile" in body_lc:
        return QuotaUsage(
            remaining_ratio=None,
            remaining_units=None,
            total_units=None,
            resets_at=None,
            unit_kind="unavailable",
            detail=(
                "quota endpoint rejected credential: missing user:profile scope; "
                "refresh the Claude Code login/setup token before using auth quota"
            ),
        )
    if exc.code == 429:
        return QuotaUsage(
            remaining_ratio=None,
            remaining_units=None,
            total_units=None,
            resets_at=None,
            unit_kind="unavailable",
            detail="quota endpoint rate-limited the preflight read; retry auth quota later",
        )
    return None


def _normalize_oauth_usage(payload: object) -> QuotaUsage | None:
    """Translate the ``/api/oauth/usage`` response shape into ``QuotaUsage``.

    Picks the more-binding (highest utilization) of ``five_hour`` and
    ``seven_day`` windows. Handles both 0.0-1.0 and 0-100 utilization
    scales by treating any value > 1.0 as a percent.
    """
    if not isinstance(payload, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for window_name in ("five_hour", "seven_day"):
        window = payload.get(window_name)
        if not isinstance(window, dict):
            continue
        util_raw = window.get("utilization")
        if not isinstance(util_raw, (int, float)):
            continue
        util = float(util_raw)
        ratio = util / 100.0 if util > 1.0 else util
        ratio = max(0.0, min(1.0, ratio))
        candidates.append(
            {
                "window": window_name,
                "ratio": ratio,
                "resets_at_iso": window.get("resets_at"),
            }
        )
    if not candidates:
        return None
    binding = max(candidates, key=lambda c: c["ratio"])
    remaining_ratio = max(0.0, 1.0 - binding["ratio"])
    resets_at_epoch: int | None = None
    resets_at_iso = binding["resets_at_iso"]
    if isinstance(resets_at_iso, str):
        try:
            dt = datetime.fromisoformat(resets_at_iso)
            resets_at_epoch = int(dt.timestamp())
        except ValueError:
            resets_at_epoch = None
    detail_parts = [
        f"{c['window']}: {int(round((1.0 - c['ratio']) * 100))}% remaining" for c in candidates
    ]
    return QuotaUsage(
        remaining_ratio=remaining_ratio,
        remaining_units=None,
        total_units=None,
        resets_at=resets_at_epoch,
        unit_kind="window-utilization-live",
        detail=" / ".join(detail_parts),
    )
