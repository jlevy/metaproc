"""Adapter protocol and shared types."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, Protocol, cast, runtime_checkable


@dataclass
class AuthStatus:
    """Result of an adapter auth check."""

    adapter_type: str
    cli_found: bool
    cli_path: str | None
    credentials_found: bool
    auth_mode: str
    details: str
    setup_hint: str


@dataclass(frozen=True)
class ConfigRejection:
    """One adapter-config key rejected during planner validation."""

    key: str
    reason: str


def resolve_templates(text: str, variables: dict[str, str]) -> str:
    """Resolve {{KEY}} placeholders in text."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\{\{(\w+)\}\}", _replace, text)


#: Forced into every agent child's seed environment, overriding operator settings.
#: An agent CLI here runs headless and writes a JSONL transcript that machines read
#: back -- `extract_log_error` mines it for failure causes and retry classification
#: acts on what it finds -- so terminal styling has no reader and is not harmless:
#: a terminal-capability warning one CLI printed at startup was repeatedly surfaced
#: as the "cause" of step failures whose real causes lay elsewhere. The keys cover
#: the conventions the ecosystem actually checks: ``NO_COLOR`` (informal standard,
#: presence means off), ``FORCE_COLOR=0`` (Node/chalk; an explicit 0 beats an
#: operator shell exporting it on), ``CLICOLOR``/``CLICOLOR_FORCE`` (BSD), and
#: ``TERM=dumb`` so terminfo-driven tools never negotiate capabilities at all.
AGENT_ENV_OVERRIDES: Final[dict[str, str]] = {
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "CLICOLOR": "0",
    "CLICOLOR_FORCE": "0",
    "TERM": "dumb",
}


def agent_seed_env() -> dict[str, str]:
    """Return the environment an agent child's env is built from.

    A copy of the process environment with terminal styling forced off, handed to
    :meth:`Adapter.prepare_env` in place of a bare ``os.environ`` copy. Everything
    else (credentials, PATH, metaproc settings) is inherited unchanged, and a step's
    authored ``env`` block is applied after ``prepare_env``, so an author who
    deliberately sets one of these keys still wins; only the ambient operator
    environment loses.
    """
    env = dict(os.environ)
    env.update(AGENT_ENV_OVERRIDES)
    return env


@runtime_checkable
class Adapter(Protocol):
    """Translates merged adapter config into a launchable CLI command."""

    adapter_type: str
    short_name: str
    default_model: str | None

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]: ...

    def prepare_env(
        self,
        env: dict[str, str],
        merged_config: dict[str, object],
    ) -> dict[str, str]: ...

    def working_directory(self, merged_config: dict[str, object]) -> Path | None: ...

    def parse_result_event(self, line: str) -> dict[str, object] | None: ...

    def check_auth(self) -> AuthStatus: ...

    def auth_info(self) -> str: ...

    def validate_config(self, merged_config: dict[str, object]) -> list[ConfigRejection]: ...

    def bootstrap(self, home: Path) -> None:
        """Materialize per-task filesystem state under *home* before
        ``build_command()``. Default implementation is a no-op; adapters
        that need to stage credentials or other on-disk state (e.g.,
        Claude Code's ``~/.claude/.credentials.json`` Vehicle B) override
        this. Called once per worker task after env is populated.
        """
        ...


@runtime_checkable
class TerminalResultValidator(Protocol):
    """Optional adapter hook for semantic checks on a terminal result event.

    Process exit status only proves that the CLI completed. Adapters whose
    terminal record carries stronger provenance can implement this hook to
    reject a result that violates the invocation contract.
    """

    def validate_result_event(
        self,
        event: dict[str, object],
        merged_config: dict[str, object],
    ) -> str | None: ...


class ResultEventParser(Protocol):
    """The adapter surface needed to locate terminal events in a JSONL log."""

    def parse_result_event(self, line: str) -> dict[str, object] | None: ...


def validate_terminal_result_log(
    adapter: ResultEventParser,
    log_path: Path,
    merged_config: dict[str, object],
) -> str | None:
    """Return an adapter-owned terminal-result contract failure, if any."""
    if not isinstance(adapter, TerminalResultValidator):
        return None

    terminal_event: dict[str, object] | None = None
    try:
        with log_path.open(encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                parsed = adapter.parse_result_event(line)
                if parsed is not None:
                    terminal_event = parsed
    except OSError:
        # Existing process/output checks own unreadable or missing logs. This hook
        # only strengthens a terminal event that the adapter actually emitted.
        return None

    if terminal_event is None:
        return None
    return adapter.validate_result_event(terminal_event, merged_config)


def parse_jsonl_event(line: str, event_type: str) -> dict[str, object] | None:
    """Parse a JSONL line and return it if its ``type`` matches *event_type*."""
    try:
        event: Any = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(event, dict) and cast("dict[str, Any]", event).get("type") == event_type:
        return cast("dict[str, object]", event)
    return None


# ─── Auth-pool types (plan-2026-04-21-auth-credential-pool.md §Design) ──────


class FailureSeverity:
    """Dispatch-verdict axis for auth failures (plan §Phase 5).

    Orthogonal to :attr:`AuthFailureClassification.status`. ``status``
    drives pool transitions (cooling / expired); ``severity`` drives
    dispatch decisions (retry immediately / wait for reset / abort).

    The two axes disagree exactly when the credential is healthy but
    the dispatch has a different problem — the case known-bug
    detection addresses.

    Declared as a class with string constants (not a StrEnum) so tests
    and classifiers that build ``AuthFailureClassification`` inline
    don't need a second enum import and plain string comparison /
    serialization works without coercion.
    """

    RETRY_NOW: ClassVar[str] = "retry-now"
    """Transient; immediate retry is the right move. Network failures,
    upstream 5xx, conn reset. Pool state unchanged; generic
    retry-with-backoff handles it."""

    RETRY_AFTER_WAIT: ClassVar[str] = "retry-after-wait"
    """Recoverable but requires waiting. Quota / rate-limit family
    — 429, overloaded_error, Anthropic ratelimit headers, Pacific
    reset phrases. Pool marks the label cooling; dispatch either
    fallbacks or waits."""

    ABORT: ClassVar[str] = "abort"
    """Permanent / operator action required. ``invalid_grant``,
    ``authentication_error``, ``billing_error``, known-bug
    signatures, schema mismatches. Pool marks the label expired when
    it's a credential issue; dispatch fails fast otherwise so
    operators see the bug instead of burning retries on it."""


def default_severity_for_status(status: str) -> str:
    """Map a pool-state status to its default dispatch severity.

    ``cooling`` → RETRY_AFTER_WAIT (quota will reset).
    ``expired`` → ABORT (operator must re-auth).
    ``ok`` / ``unknown`` → RETRY_NOW (either we're healthy or we
    don't know; default to the less-destructive option and let the
    generic retry classifier own the real decision from there).
    """
    if status == "cooling":
        return FailureSeverity.RETRY_AFTER_WAIT
    if status == "expired":
        return FailureSeverity.ABORT
    return FailureSeverity.RETRY_NOW


@dataclass(frozen=True)
class AuthFailureClassification:
    """Outcome of an auth-capable adapter's failure classification.

    Distinct from ``metaproc.engine.retry.FailureClass`` which classifies
    generic run-parallel retry verdicts. The auth classification is
    consumed by the slot coordinator to decide lease transitions and
    fallback; the retry classifier is consumed by
    ``_classify_and_maybe_retry`` to decide whether to reschedule. The
    two are orthogonal: a cooling auth failure is a RETRY verdict that
    also triggers a slot swap before the next attempt.

    ``cooling_until_ts`` is UTC epoch seconds. ``None`` means cooling
    indefinitely until operator probe; selection treats it as
    ineligible. ``reason`` is free-form for logs and never contains
    the payload.

    ``severity`` (plan Phase 5) is the dispatch-verdict axis:
    RETRY_NOW / RETRY_AFTER_WAIT / ABORT. ``None`` means "derive from
    status" (see :meth:`effective_severity` and
    :func:`default_severity_for_status`) — older classifiers not yet
    updated for Phase 5 keep working via the status→severity default.

    ``known_bug_signature`` (plan P5.3) is populated when the failure
    matches a :class:`~metaproc.dispatch.known_bugs.KnownBugSignature`.
    Operators grep logs for this field to aggregate re-occurrences.
    Setting this field always forces ``effective_severity() == ABORT``
    — we don't retry past a known bug.
    """

    status: Literal["ok", "cooling", "expired", "unknown"]
    cooling_until_ts: int | None = None
    reason: str = ""
    severity: str | None = None
    known_bug_signature: str | None = None

    # HTTP-axis signals from the adapter's CLI debug output. Adapters
    # that surface these populate them; absent / ``None`` means "signal
    # not applicable or not observed". Carried through to AuthOutcome.
    api_status: int | None = None
    oauth_refresh_status: int | None = None
    request_id: str = ""
    error_body_excerpt: str = ""
    retry_after_s: int | None = None

    def effective_severity(self) -> str:
        """Return the dispatch verdict — explicit severity, or derived.

        Rules (in order):
        1. Known-bug signature → ABORT (bugs always abort).
        2. Explicit ``severity`` → return it verbatim.
        3. Fall back to :func:`default_severity_for_status`.
        """
        if self.known_bug_signature is not None:
            return FailureSeverity.ABORT
        if self.severity is not None:
            return self.severity
        return default_severity_for_status(self.status)


@dataclass(frozen=True)
class QuotaUsage:
    """Current quota usage reported by an auth-capable adapter.

    ``remaining_ratio`` is in [0.0, 1.0]. ``None`` fields mean the
    adapter reports one half of the ratio but not both total and
    remaining. ``resets_at`` is UTC epoch seconds; ``None`` means no
    reset is known. ``unit_kind`` is free-form ("tokens", "messages",
    "requests"). ``detail`` is an optional free-form summary string;
    never contains the payload.
    """

    remaining_ratio: float | None
    remaining_units: int | None
    total_units: int | None
    resets_at: int | None
    unit_kind: str
    detail: str = ""


@runtime_checkable
class AuthCapableCliAdapter(Adapter, Protocol):
    """Adapter that can participate in the labeled credential pool.

    Extends ``Adapter`` with the surface required by
    ``metaproc.dispatch.credential_pool`` and the slot coordinator:
    slot-local credential materialization, failure classification, and
    optional quota querying. Only adapters that need credential
    injection implement this (Claude Code, Codex, Gemini); adapters
    that authenticate out-of-band (e.g. Pi via ``ANTHROPIC_API_KEY``)
    do not. Use ``metaproc.adapters.registry.get_auth_capable`` to
    probe, never ``hasattr``.
    """

    slot_credential_filename: str
    """Per-adapter native credential filename written under the slot dir.

    Claude Code: ``.credentials.json``. Codex: the adapter writes
    ``.codex/auth.json`` relative to the slot; this field still names
    the user-visible blob filename for logs and flush diffs.
    """

    compatible_fallback_adapters: list[str]
    """Adapters this one is cross-provider-compatible with for fallback.

    Walked by the slot coordinator in order after same-provider
    candidates are exhausted, when ``FallbackPolicy`` is
    ``cross-provider`` or ``both``. Empty list means no cross-provider
    fallback from this adapter. Compatibility is a declaration about
    this adapter's request/response surface being substitutable for
    metaproc steps — not a statement about model quality.
    """

    def credential_scope_env(
        self,
        slot_dir: Path,
        *,
        vehicle: Any = None,
        blob: str = "",
    ) -> dict[str, str]:
        """Env vars that scope this CLI's credential lookup to *slot_dir*.

        Claude Code (Vehicle B): ``{"CLAUDE_CONFIG_DIR": str(slot_dir)}``.
        Claude Code (Vehicle A — schema_version 2): scopes the slot AND
        injects ``CLAUDE_CODE_OAUTH_TOKEN=<blob>`` plus the static-bearer
        hardening flags (``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1``,
        ``DISABLE_UPDATES=1``). The subprocess sees the static token and
        no ``.credentials.json`` is materialized.
        Codex / Gemini: ignore *vehicle* (single-vehicle adapters).

        ``vehicle`` is typed ``Any`` here to avoid an import cycle with
        ``metaproc.dispatch.credential_pool.Vehicle``; adapters import it
        at use site. ``vehicle=None`` means "current call site has no
        vehicle awareness yet" and adapters MUST treat that as the
        legacy Vehicle B path so existing callers (slot coordinator
        before Phase 3, ``auth check``, etc.) keep working unchanged.
        """
        ...

    def credential_scrub_env(
        self,
        *,
        vehicle: Any = None,
    ) -> dict[str, str]:
        """Env vars to unset (value ``""``) before launching a pool slot.

        Higher-precedence vars in the CLI's auth chain that would
        silently out-vote the slot credential. Each ``""`` value is
        interpreted by the launcher as "unset". Does NOT include vars
        that indicate enterprise-managed auth (e.g. ``ANTHROPIC_API_KEY``)
        — those cause the coordinator to refuse slot acquisition with a
        warning rather than silently clobbering operator intent.

        Vehicle-aware (schema_version 2): under Vehicle A the static
        ``CLAUDE_CODE_OAUTH_TOKEN`` is the credential, so adapters omit
        it from the scrub set even though it is otherwise scrubbed.
        """
        ...

    def materialize_credential(
        self,
        slot_dir: Path,
        blob: str,
        *,
        vehicle: Any = None,
    ) -> None:
        """Materialize the credential into the slot directory.

        Vehicle B (default): writes ``slot_dir / self.slot_credential_filename``
        mode 0600 plus any adapter-specific metadata (Codex writes
        ``slot_dir/.codex/auth.json`` and a minimal ``config.toml``).
        Creates ``slot_dir`` mode 0700 if missing. Never touches
        ~/.claude/, ~/.codex/, or the OS Keychain.

        Vehicle A (schema_version 2): no file is written — the
        credential is injected as ``CLAUDE_CODE_OAUTH_TOKEN`` via
        :meth:`credential_scope_env`. Adapters still create ``slot_dir``
        mode 0700 and verify it is empty of ``.credentials.json`` /
        ``settings.json`` (defense in depth: a leaked ``apiKeyHelper``
        in a settings file would silently override Vehicle A in the
        precedence chain).
        """
        ...

    def capture_credential(self) -> str:
        """Read the native credential from the host for a pool push.

        Reads the operator's live credential state — typically Keychain
        on macOS, ``~/.claude/.credentials.json`` on Linux. Raises
        ``RuntimeError`` with an operator-actionable message on miss.
        Used only by ``metaproc auth push``.
        """
        ...

    def classify_failure(
        self,
        exc: BaseException | None,
        stderr: str,
        session_log_path: Path | None,
    ) -> AuthFailureClassification:
        """Map an adapter failure to a pool state transition + reset timestamp.

        ``session_log_path`` is the per-step CLI session log (e.g.
        ``.logs/tool-adhoc_<item>_<ts>.jsonl``). When the CLI emits
        HTTP response metadata in the session log
        (``anthropic-ratelimit-*-reset`` / ``retry-after``), prefer
        those as the source of ``cooling_until_ts``. Otherwise fall
        back to parsing the human-readable error body (Claude:
        ``"resets Xam (America/Los_Angeles)"``).
        """
        ...

    def flush_refreshed_credential(self, slot_dir: Path) -> str | None:
        """Return the current on-disk blob if it differs from bootstrap.

        Called post-run, before the slot is torn down. The worker
        compares fingerprint against the bootstrapped blob and, if
        rotated, writes back to the pool under the label's lease before
        releasing. Returns ``None`` if no write-back is required.
        """
        ...

    def debug_capture_args(self, slot_dir: Path) -> list[str]:
        """Return CLI args that capture API-level debug output to *slot_dir*.

        Workers append these to the launched command so OAuth
        refresh-attempt boundaries, AxiosError details, and per-attempt
        API status are written to a file in the slot dir. The slot
        teardown reads the file before ``rm -rf`` to populate the
        :class:`AuthOutcome` event with the real error string instead of
        the static known-bug template name. Adapters without a
        request-side debug capture flag return ``[]``.

        The filenames the args write to must be reported by
        :meth:`diagnostic_filenames` so the orchestrator can preserve
        them under the run's standard ``.logs/`` directory after the
        slot is torn down.
        """
        ...

    def diagnostic_filenames(self) -> tuple[str, ...]:
        """Return slot-relative filenames the adapter writes for diagnostics.

        These files are written by the worker during the run (e.g.
        through :meth:`debug_capture_args`) and are NOT credential
        material — the orchestrator copies them out of the slot dir
        into the run's ``.logs/`` directory just before slot teardown,
        so post-mortem can correlate API-level debug output with the
        captured stream-json session log. Returns ``()`` for adapters
        that don't write any (current default for codex / gemini /
        pi until they grow equivalents to ``claude -d api``).

        Filenames are namespaced by adapter (e.g. ``claude-code-debug.log``
        rather than ``debug.log``) so a future multi-adapter slot or
        side-by-side comparison stays unambiguous in the operator-facing
        logs tree.
        """
        return ()

    def setup_token_command(self) -> list[str] | None:
        """Return argv for an interactive Vehicle A setup-token mint, or None.

        Adapters that support ``metaproc auth setup``'s interactive
        token-mint path return the argv to spawn — e.g.
        ``ClaudeCodeCliAdapter`` returns ``["claude", "setup-token"]``.
        ``setup_cmd`` runs that subprocess with stdout captured (the
        token) and stderr passed through to the operator's terminal so
        the OAuth flow is visible.

        Adapters with no setup-token equivalent today (codex, gemini)
        return ``None``; ``auth setup`` refuses with a message listing
        the adapters that do support the flow. When those adapters
        grow an equivalent, override here — no ``auth setup`` change.
        """
        return None

    def query_quota_usage(self, slot_dir: Path) -> QuotaUsage | None:
        """Return current quota usage for the credential staged in *slot_dir*.

        Adapters that expose a native usage endpoint (Claude Code CLI)
        hit it in-process using the bootstrapped credential and return
        a ``QuotaUsage``. Adapters without one return ``None`` — the
        generic caller treats ``None`` as "unsupported for this
        adapter" and renders ``—`` in list tables.

        Called opportunistically: on ``metaproc auth list --usage``, on
        ``metaproc auth usage``, and as a selection tiebreak when
        multiple eligible labels are available and the adapter supports
        it. Must not mutate the slot or write to the pool. Must be safe
        to call for a ``cooling`` label (to see how much longer it's
        cooling). Never prints or returns the blob. Errors are
        swallowed and returned as ``None`` — quota queries are
        best-effort, never blocking.
        """
        ...

    def query_live_quota(
        self,
        slot_dir: Path,
        *,
        vehicle: Any = None,
        blob: str = "",
    ) -> QuotaUsage | None:
        """Return live per-account quota by hitting the vendor's HUD endpoint
        for the credential staged in *slot_dir*.

        Distinct from :meth:`query_quota_usage` (which may derive from local
        rate-limit-event records): this is a network call against the
        vendor's own quota-rendering endpoint, working before the first
        429 of a run. Required for the ``metaproc auth quota`` pre-dispatch
        gate.

        Returns ``None`` when:

        - the adapter does not expose a live endpoint;
        - the network call fails;
        - the credential cannot be read from the slot or pool blob.

        Errors are swallowed — callers fall back to
        :meth:`query_quota_usage` for the derived signal. Must not mutate
        the slot. Never returns or prints the blob.
        """
        return None
