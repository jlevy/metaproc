"""Labeled credential-pool commands.

The same Typer surface drives the GCP Secret Manager and local-filesystem
backends through :class:`metaproc.dispatch.credential_pool.PoolBackend`.
Credential payloads are never printed; only labels, timestamps, and fingerprints leave
this module through stdout.
"""

from __future__ import annotations

import getpass
import json as _json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace as _replace
from pathlib import Path
from typing import Any, cast

import typer

from metaproc import paths as paths_mod
from metaproc.adapters.base import AuthCapableCliAdapter
from metaproc.adapters.registry import ADAPTER_REGISTRY, get_auth_capable
from metaproc.cli import app, get_output
from metaproc.commands.helpers import load_process_spec
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_usage import aggregate_label_usage_for_run
from metaproc.dispatch.auth_usage_render import (
    doctor_report_to_dict,
    render_doctor_text,
    render_run_usage_text,
    usage_report_to_dict,
)
from metaproc.dispatch.credential_pool import (
    EntryState,
    PoolBackend,
    PoolEntry,
    QuotaGroup,
    Vehicle,
    fingerprint_blob,
    gcp_backend,
    local_backend,
    safe_apply_state,
    state_mark_expired,
    state_mark_ok,
    validate_operator_label,
)
from metaproc.dispatch.pool_status import build_pool_status, render_text, to_json_dict
from metaproc.dispatch.preflight import (
    GuardPosture,
    check_dispatch_auth_env,
    check_step_preflight,
    validate_guard_posture,
)
from metaproc.io import iter_jsonl_objects, to_yaml_string

log = logging.getLogger(__name__)

auth_app = typer.Typer(
    name="auth",
    help=(
        "Manage labeled CLI adapter credentials in the pool "
        "(GCP Secret Manager or local filesystem)."
    ),
    no_args_is_help=True,
)
app.add_typer(auth_app)


# ── Injection seam for tests ─────────────────────────────────────
#
# Production constructs a GcpSecretManagerBackend via
# :func:`gcp_backend`.  Tests swap :data:`BACKEND_FACTORY` via
# monkeypatch to return an in-memory backend so no live GCP is hit.
# Keep this in module scope (not a class attribute) so the override
# survives Typer's command-time re-import on reload.


def _default_backend_factory(
    backend: str,
    *,
    pool_user: str,
) -> PoolBackend:
    if backend == "gcp-secret-manager":
        project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
        if not project:
            msg = (
                "METAPROC_GCP_PROJECT is not set. "
                "Export METAPROC_GCP_PROJECT or use --backend local."
            )
            raise typer.BadParameter(msg)
        return gcp_backend(project, pool_user=pool_user)
    if backend == "local":
        return local_backend()
    msg = f"unknown --backend {backend!r}: expected 'gcp-secret-manager' or 'local'"
    raise typer.BadParameter(msg)


BACKEND_FACTORY = _default_backend_factory


# ── Shared option helpers ────────────────────────────────────────


def _resolve_pool_user(user: str | None) -> str:
    """Resolve the pool owner: flag → ``METAPROC_AUTH_POOL`` env → ``$USER``."""
    if user:
        return user
    env_user = MetaprocEnv.METAPROC_AUTH_POOL.read_str(default="") if _has_pool_env() else ""
    if env_user:
        return env_user
    return getpass.getuser()


def _has_pool_env() -> bool:
    # MetaprocEnv is a growing enum; feature-detect instead of crashing
    # on a clean install that hasn't landed the env var yet.
    return hasattr(MetaprocEnv, "METAPROC_AUTH_POOL")


def _resolve_backend_name(flag: str | None) -> str:
    """Resolve backend: flag → env → default.

    Default is ``gcp-secret-manager`` for Phase 1 (local backend lands
    in Phase 2b). Once the local backend is wired, the default for
    non-cloud flows becomes ``local``.
    """
    if flag:
        return flag
    if hasattr(MetaprocEnv, "METAPROC_AUTH_BACKEND"):
        env = MetaprocEnv.METAPROC_AUTH_BACKEND.read_str(default="")
        if env:
            return env
    return "gcp-secret-manager"


def _require_auth_capable(adapter: str):  # noqa: ANN201
    adapter_impl = get_auth_capable(adapter)
    if adapter_impl is None:
        msg = (
            f"adapter {adapter!r} is not auth-capable (does not implement "
            "AuthCapableCliAdapter). Pool operations are only meaningful "
            "for adapters that materialize mutable CLI OAuth blobs."
        )
        raise typer.BadParameter(msg)
    return adapter_impl


@contextmanager
def _temporary_environ(env: dict[str, str]) -> Generator[None]:
    """Temporarily replace process env for in-process adapter probes."""
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


# ── auth probe helpers ─────────────────────────────────────────────────
#
# The HTTP-signal parser lives in ``adapters.claude_code`` so the
# dispatch-time classifier and the operator probe share one set of regexes.

from dataclasses import dataclass as _dataclass  # noqa: E402

from metaproc.adapters.claude_code import (  # noqa: E402
    ClaudeApiSignals,
    parse_claude_api_signals,
)


@_dataclass(frozen=True)
class _ProbeTransition:
    """Decided pool-state target + operator-facing note."""

    target_status: str  # active | expired | cooling | unknown
    note: str


def _classify_probe_result(*, parsed: ClaudeApiSignals, exit_code: int) -> _ProbeTransition:
    """Map parsed probe signals to a pool-state target."""
    # OAuth refresh failure → expired regardless of subsequent API status
    # (the access token will never refresh again on its own).
    if parsed.oauth_refresh_status is not None and parsed.oauth_refresh_status >= 400:
        return _ProbeTransition(
            target_status="expired",
            note=(
                f"OAuth refresh returned {parsed.oauth_refresh_status} — refresh token "
                "rejected by Anthropic; pool blob needs `metaproc auth push --label <X>` "
                "after re-auth via `claude /login`."
            ),
        )
    if parsed.api_status == 401:
        return _ProbeTransition(
            target_status="expired",
            note="API returned 401 authentication_error; access token rejected.",
        )
    if parsed.api_status == 429:
        return _ProbeTransition(
            target_status="cooling",
            note=(
                "API returned 429"
                + (f" with retry_after={parsed.retry_after_s}s" if parsed.retry_after_s else "")
            ),
        )
    if exit_code == 0 and parsed.api_status is None:
        return _ProbeTransition(
            target_status="active",
            note="claude --print returned successfully; access token is valid.",
        )
    return _ProbeTransition(
        target_status="unknown",
        note=f"unexpected probe outcome (exit={exit_code}, api_status={parsed.api_status}).",
    )


def _apply_probe_state_transition(
    *,
    pool_backend: PoolBackend,
    adapter: str,
    label: str,
    entry: PoolEntry,
    target_status: str,
) -> EntryState | None:
    """Apply a probe-derived state transition to the pool, race-tolerant.

    Returns the state actually written, or ``None`` when the probe
    outcome doesn't trigger a state write at all (``unknown`` /
    ``cooling`` paths) or the racing CAS retries were exhausted.
    """
    if target_status == "active":
        compute_new = state_mark_ok
    elif target_status == "expired":
        compute_new = state_mark_expired
    else:
        return None

    return safe_apply_state(
        pool_backend,
        adapter=adapter,
        label=label,
        entry=entry,
        compute_new=compute_new,
    )


def _render_entry_row(entry: PoolEntry) -> dict[str, str]:
    return {
        "adapter": entry.adapter,
        "label": entry.label,
        "status": entry.state.status,
        "vehicle": entry.state.vehicle.value,
        "fp": entry.state.fp,
        "account_id": entry.state.account_id or "",
        "quota_group": _format_quota_group(entry.state.quota_group),
        "last_ok": _format_ts(entry.state.last_ok_ts),
        "last_quota": _format_ts(entry.state.last_quota_ts),
        "cooling_until": _format_ts(entry.state.cooling_until_ts),
    }


def _format_quota_group(qg: QuotaGroup) -> str:
    """Render a QuotaGroup as ``org:abc12345…`` / ``account:def67890…`` / ``unknown``."""
    if qg.kind == "unknown" or not qg.value:
        return "unknown"
    # Truncate UUIDs / 16-hex hashes to first 8 chars + ellipsis for readability.
    short = qg.value[:8] + "…" if len(qg.value) > 8 else qg.value
    return f"{qg.kind}:{short}"


# ── Vehicle A token sourcing + quota-group resolution ───────────


def _source_oauth_token(token_file: Path | None) -> str:
    """Return a Vehicle A token from --token-file, stdin, or env (in that order).

    Raises :class:`typer.BadParameter` when no source provides a token.
    Whitespace is stripped so a token captured into a file via
    ``claude setup-token > token.txt`` (which appends a newline)
    round-trips cleanly.
    """

    if token_file is not None:
        try:
            raw = token_file.read_text()
        except OSError as exc:
            msg = f"--token-file {token_file}: {exc}"
            raise typer.BadParameter(msg) from exc
        token = raw.strip()
        if not token:
            msg = f"--token-file {token_file} is empty"
            raise typer.BadParameter(msg)
        return token
    if not sys.stdin.isatty():
        # Stdin is piped or redirected; read the token from there.
        token = sys.stdin.read().strip()
        if token:
            return token
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    msg = (
        "vehicle=oauth-token requires a token source: pass --token-file <path>, "
        "pipe the token via stdin, or set CLAUDE_CODE_OAUTH_TOKEN in the environment."
    )
    raise typer.BadParameter(msg)


def _resolve_quota_group(
    spec: str,
    *,
    organization_uuid: str | None,
    account_id: str | None,
) -> QuotaGroup:
    """Resolve a ``--quota-group`` flag value to a :class:`QuotaGroup`.

    Accepted forms:
    - ``"auto"`` (default) — derive from organization_uuid / account_id /
      unknown, in that order.
    - ``"org:<uuid>"`` — explicit org grouping.
    - ``"account:<hash>"`` — explicit account grouping.
    - ``"unknown"`` — explicit unknown (rare; useful for tests / pinning).
    """
    if spec == "auto":
        if organization_uuid:
            return QuotaGroup(kind="org", value=organization_uuid)
        if account_id:
            return QuotaGroup(kind="account", value=account_id)
        return QuotaGroup.unknown()
    if spec == "unknown":
        return QuotaGroup.unknown()
    if ":" not in spec:
        msg = (
            f"--quota-group {spec!r}: expected 'auto', 'unknown', 'org:<uuid>', or 'account:<hex>'"
        )
        raise typer.BadParameter(msg)
    kind, _, value = spec.partition(":")
    if kind not in ("org", "account"):
        msg = f"--quota-group {spec!r}: kind must be 'org' or 'account', got {kind!r}"
        raise typer.BadParameter(msg)
    if not value:
        msg = f"--quota-group {spec!r}: value after '{kind}:' is empty"
        raise typer.BadParameter(msg)
    return QuotaGroup(kind=kind, value=value)  # type: ignore[arg-type]


def _resolve_default_vehicle(adapter: str, flag: str | None) -> Vehicle:
    """Resolve the ``--vehicle`` flag to a :class:`Vehicle`.

    For ``claude-code-cli``, the default is ``oauth-token`` (Vehicle A —
    the static-bearer ``CLAUDE_CODE_OAUTH_TOKEN`` minted by
    ``claude setup-token``). This is the only path metaproc deploys
    today; ``--vehicle login-credentials`` remains explicitly available
    as an escape hatch for one-off bootstrap or for accounts that fail
    Vehicle A on the pinned CLI version, but it isn't the default.

    For non-Claude adapters that don't have a Vehicle A equivalent
    (codex's ChatGPT-OAuth, gemini's API key), the default stays at
    ``login-credentials`` (their only meaningful vehicle).
    """
    if flag is None:
        if adapter == "claude-code-cli":
            return Vehicle.OAUTH_TOKEN
        return Vehicle.LOGIN_CREDENTIALS
    if flag == "login-credentials":
        return Vehicle.LOGIN_CREDENTIALS
    if flag == "oauth-token":
        return Vehicle.OAUTH_TOKEN
    msg = f"--vehicle {flag!r}: expected 'oauth-token' or 'login-credentials'"
    raise typer.BadParameter(msg)


# ── auth setup helpers ────────────────────────────────────────────
#
# `auth setup` is the operator-facing one-step Vehicle A onboarding
# command — it wraps token-mint (interactive `claude setup-token` or
# piped) + push-to-both-backends + probe. The helpers below are
# extracted so they're individually unit-testable.


def _stdin_is_interactive() -> bool:
    """Return True iff stdin is a TTY (no pipe / redirect).

    Extracted as a module-level callable so tests can monkey-patch this
    seam directly. ``Click`` ``CliRunner.invoke`` replaces ``sys.stdin``
    inside the ``isolation()`` context, so a ``monkeypatch.setattr(
    sys.stdin, "isatty", ...)`` set up before invoke is silently
    discarded by Click. Patching ``auth_mod._stdin_is_interactive``
    instead works because the module-level binding is what
    ``_source_setup_token`` reads.
    """

    return sys.stdin.isatty()


def _source_setup_token(*, token_file: Path | None, mint_command: list[str] | None) -> str:
    """Source the OAuth token for ``auth setup``.

    Precedence: ``--token-file`` → piped stdin → spawn *mint_command*
    interactively. The third path runs only when stdin is a TTY (no
    pipe) AND *mint_command* is non-None — it spawns a subprocess
    attached to the operator's terminal so the OAuth flow is visible.

    *mint_command* comes from the adapter's
    :meth:`AuthCapableCliAdapter.setup_token_command`, e.g.
    ``["claude", "setup-token"]`` for Claude Code. Adapters without
    a setup-token equivalent return ``None`` — ``setup_cmd`` filters
    those out before reaching this helper, but we tolerate ``None``
    here as a defensive guard.

    Token-handling discipline (the security properties ``auth setup``
    promises):
    - Returned in-memory only; never written to disk by this helper.
    - When spawning the mint command, stderr is left attached to the
      operator's terminal so prompts/errors are visible — but stdout
      (the token) is captured into the returned string and never echoed.
    - Empty / failed mints raise ``typer.Exit(1)`` with operator-
      actionable messages that never include the token.
    """

    if token_file is not None:
        try:
            raw = token_file.read_text()
        except OSError as exc:
            msg = f"--token-file {token_file}: {exc}"
            raise typer.BadParameter(msg) from exc
        token = raw.strip()
        if not token:
            msg = f"--token-file {token_file} is empty"
            raise typer.BadParameter(msg)
        return token

    if not _stdin_is_interactive():
        token = sys.stdin.read().strip()
        if not token:
            msg = (
                "stdin is empty; expected a CLAUDE_CODE_OAUTH_TOKEN piped in. "
                "Either pipe a setup-token, pass --token-file, or run "
                "`metaproc auth setup <label>` interactively (no pipe) so the "
                "adapter's mint command spawns automatically."
            )
            raise typer.BadParameter(msg)
        return token

    # Interactive: spawn the adapter's mint command attached to the
    # operator's terminal. stderr passes through so operator sees the
    # OAuth flow; stdout is captured.
    out = get_output()
    if mint_command is None:
        # Defensive — caller should have refused before reaching here.
        out.error(
            "this adapter has no interactive setup-token command; "
            "supply a token via --token-file or stdin."
        )
        raise typer.Exit(code=1)
    out.progress(
        f"Running `{' '.join(mint_command)}` (interactive — follow the "
        "OAuth flow in your terminal/browser)…"
    )

    try:
        result = subprocess.run(
            mint_command,
            stdout=subprocess.PIPE,
            # stderr=None: passes through to the operator's terminal so
            # the OAuth prompts and any error output are visible. The
            # token comes back via stdout, captured into result.stdout.
            text=True,
            timeout=600,  # generous — operator is browsing OAuth pages
            check=False,
        )
    except FileNotFoundError as exc:
        out.error(f"`{mint_command[0]}` not found on PATH. Install the adapter CLI and try again.")
        raise typer.Exit(code=1) from exc
    if result.returncode != 0:
        out.error(f"`{' '.join(mint_command)}` failed (exit {result.returncode}).")
        raise typer.Exit(code=1)
    raw = result.stdout or ""
    if not raw.strip():
        out.error(f"`{' '.join(mint_command)}` produced no token. Did you complete the OAuth flow?")
        raise typer.Exit(code=1)
    token = _extract_oauth_token_from_mint_stdout(raw)
    if token is None:
        # The mint command emitted output but we couldn't find a
        # token-shaped string. Surface a redacted preview so the
        # operator can debug without the token leaking — and point
        # them at the file/stdin alternatives.
        preview = "".join(c for c in raw if c.isprintable())[:200]
        out.error(
            f"`{' '.join(mint_command)}` did not emit a recognizable OAuth token "
            "on stdout. The command may have shown the token in a TUI rather than "
            "printed it to a pipe. Try: run `claude setup-token` directly, copy "
            "the token, then re-run as "
            f"`echo '<token>' | metaproc auth setup <label>` or "
            "`metaproc auth setup <label> --token-file <path>`. "
            f"stdout preview (first 200 printable chars): {preview!r}"
        )
        raise typer.Exit(code=1)
    return token


# Claude OAuth tokens minted by `claude setup-token` carry an
# Anthropic ``sk-ant-<subtype>-...`` prefix where ``<subtype>`` is
# alphanumeric and version-tagged (today: ``oat01``; the regex stays
# version-agnostic so a future ``oat02`` / similar bump doesn't
# silently break extraction). The body is URL-safe base64 + a few
# extras; the ``{20,}`` minimum prevents the pattern from matching
# fragments like ``sk-ant-foo-bar`` that a future banner could
# include accidentally.
_CLAUDE_OAUTH_TOKEN_RE = re.compile(r"sk-ant-[a-z0-9]+-[A-Za-z0-9_/+=-]{20,}")


def _extract_oauth_token_from_mint_stdout(raw: str) -> str | None:
    """Pull the OAuth token out of ``claude setup-token`` stdout.

    The CLI in v2.1.119 writes the OAuth token surrounded by an ASCII
    banner, ANSI escape codes, congratulatory text, and "store this
    securely" instructions. The token itself is the only segment
    matching :data:`_CLAUDE_OAUTH_TOKEN_RE` (an Anthropic
    ``sk-ant-<subtype>-<body>`` token) — extract by regex rather than
    trying to parse the TUI structure.

    Returns the longest match (defensive against partial overlaps in
    the regex's greedy behavior) or ``None`` if no token-shaped
    substring is present.
    """
    matches = _CLAUDE_OAUTH_TOKEN_RE.findall(raw)
    if not matches:
        return None
    # The CLI emits the token exactly once, but pick the longest
    # match defensively in case the TUI banner contains a partial-
    # token substring that the greedy regex picks up.
    return max(matches, key=len)


def _adapters_supporting_setup() -> list[str]:
    """Return adapter type names whose adapter declares a setup-token command.

    Used in the error message when the operator passes ``--adapter X``
    where X has no mint command — we list the supported alternatives
    so the operator knows what to switch to.
    """

    supported: list[str] = []
    for name, adapter in ADAPTER_REGISTRY.items():
        cmd = getattr(adapter, "setup_token_command", None)
        if callable(cmd) and cmd() is not None:
            supported.append(name)
    return sorted(supported)


def _resolve_setup_backends(backend_flag: str) -> list[str]:
    """Resolve ``auth setup``'s ``--backend`` into a list of backends.

    ``both`` (the default) graceful-degrades: ``local`` is always
    pushable; ``gcp-secret-manager`` is included only when
    ``METAPROC_GCP_PROJECT`` is set in the environment (otherwise
    ``gcp_backend`` would raise at construction). Operators with no
    GCP project see a one-backend push without an error.

    Explicit single-backend values (``local`` / ``gcp-secret-manager``)
    are passed through unchanged. Unknown values raise BadParameter.
    """
    if backend_flag in ("local", "gcp-secret-manager"):
        return [backend_flag]
    if backend_flag != "both":
        msg = f"--backend must be 'local', 'gcp-secret-manager', or 'both', got {backend_flag!r}"
        raise typer.BadParameter(msg)
    targets = ["local"]
    if MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default=""):
        targets.append("gcp-secret-manager")
    return targets


# ── Subcommands ──────────────────────────────────────────────────


@auth_app.command("push")
def push_cmd(
    adapter: str = typer.Option(..., "--adapter", help="Adapter type (e.g. claude-code-cli)."),
    label: str = typer.Option(..., "--label", help="Operator tag [a-z0-9-]{1,40}."),
    vehicle: str | None = typer.Option(
        None,
        "--vehicle",
        help=(
            "Credential vehicle: 'oauth-token' (Vehicle A — recommended primary, "
            "static CLAUDE_CODE_OAUTH_TOKEN injected via env var, no .credentials.json "
            "materialized) or 'login-credentials' (Vehicle B — refresh-rotating "
            "OAuth session snapshot, default for back-compat). See "
            "src/metaproc/docs/metaproc-design.md."
        ),
    ),
    token_file: Path | None = typer.Option(
        None,
        "--token-file",
        help=(
            "Path to a file containing a CLAUDE_CODE_OAUTH_TOKEN (Vehicle A only). "
            "Whitespace is stripped. Falls back to stdin then "
            "$CLAUDE_CODE_OAUTH_TOKEN if not provided."
        ),
    ),
    account_id: str | None = typer.Option(
        None,
        "--account-id",
        help=(
            "Stable per-account identity hash (e.g. 16 hex of sha256 over the "
            "account email). Used by the classifier's quota-group walk on 429 "
            "failover. Optional; defaults to None."
        ),
    ),
    organization_uuid: str | None = typer.Option(
        None,
        "--organization-uuid",
        help=(
            "Anthropic organizationUuid for this account, when known. Two labels "
            "with the same organizationUuid share quota (per claude-code#41886) "
            "so the classifier prefers labels in a different org on 429 failover."
        ),
    ),
    quota_group: str = typer.Option(
        "auto",
        "--quota-group",
        help=(
            "Account-level rate-limit grouping. 'auto' (default) derives from "
            "organization_uuid → account_id → unknown. Explicit forms: "
            "'org:<uuid>', 'account:<hex>', 'unknown'."
        ),
    ),
    backend: str | None = typer.Option(
        None, "--backend", help="Storage backend: local or gcp-secret-manager."
    ),
    user: str | None = typer.Option(
        None, "--user", help="Pool owner (default: $METAPROC_AUTH_POOL or $USER)."
    ),
    probe: bool = typer.Option(
        False,
        "--probe",
        help=(
            "After push, run a real-API probe (`auth probe`) against the freshly "
            "pushed credential and transition the pool entry based on the result. "
            "Same as `auth push` then `auth probe` but in one operator action so "
            "pool state can't drift from reality between commands."
        ),
    ),
    _quiet: bool = False,
) -> None:
    """Push a credential to the pool. Vehicle B reads native source (Keychain); Vehicle A reads --token-file/stdin/env.

    Idempotent: first call creates the pool entry + backend-side record;
    subsequent calls add a new version and mark the entry ``active``.
    NEVER prints the payload. With ``--probe``, immediately follows the
    push with a real-API roundtrip so the pool state reflects whether
    the new credential actually works.

    Vehicle A (``--vehicle oauth-token``): the token is sourced from
    ``--token-file`` → stdin → ``$CLAUDE_CODE_OAUTH_TOKEN`` (in that
    order). The blob stored in the pool is the bearer token string.
    Vehicle B (default): the adapter reads from its native source
    (macOS Keychain on darwin, ``~/.claude/.credentials.json`` on
    Linux for claude-code-cli; ``$CODEX_CREDS_JSON`` for codex).

    ``_quiet`` is a Python-only kwarg (not a CLI flag) that suppresses
    the per-push summary line so a higher-level command (``auth setup``)
    can drive multiple pushes and emit one summary at the end without
    duplicate output.
    """
    out = get_output()
    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    adapter_impl = _require_auth_capable(adapter)
    resolved_vehicle = _resolve_default_vehicle(adapter, vehicle)
    if resolved_vehicle is Vehicle.OAUTH_TOKEN:
        blob = _source_oauth_token(token_file)
    else:
        # Vehicle B: read the operator's native credential (Keychain on
        # macOS, .credentials.json on Linux; codex reads its own).
        try:
            blob = adapter_impl.capture_credential()
        except RuntimeError as exc:
            out.error(str(exc))
            raise typer.Exit(code=1) from exc

    fp = fingerprint_blob(blob)
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    resolved_quota_group = _resolve_quota_group(
        quota_group,
        organization_uuid=organization_uuid,
        account_id=account_id,
    )
    # Fresh push is always active + ok-now. Rotate calls land here too;
    # the caller can follow up with disable/enable for fine-grained ops.
    base_state = EntryState(
        status="active",
        fp=fp,
        vehicle=resolved_vehicle,
        account_id=account_id,
        organization_uuid=organization_uuid,
        quota_group=resolved_quota_group,
    )
    state = state_mark_ok(base_state, new_fp=fp)
    pool_backend.upsert_entry(adapter, label, blob=blob, state=state)
    # Render without the blob. The fingerprint is the only identity
    # surface ever exposed.
    vehicle_tag = "A" if resolved_vehicle is Vehicle.OAUTH_TOKEN else "B"
    if not _quiet:
        out.summary(
            f"✓ pushed {adapter}/{label}  vehicle={vehicle_tag}  fp={fp}  backend={backend_name}",
            record={
                "adapter": adapter,
                "label": label,
                "vehicle": resolved_vehicle.value,
                "user": pool_user,
                "backend": backend_name,
                "fp": fp,
                "account_id": account_id,
                "organization_uuid": organization_uuid,
                "quota_group": _format_quota_group(resolved_quota_group),
                "status": "active",
                "last_ok_ts": state.last_ok_ts,
            },
        )
    if probe:
        # Re-invoke probe_cmd with all kwargs explicit (typer.Option
        # defaults on the function signature don't auto-apply when
        # called Python-side, only via CLI parsing).
        probe_cmd(
            adapter=adapter,
            label=label,
            backend=backend,
            user=user,
            show_headers=False,
            as_json=False,
            _quiet=_quiet,
        )


@auth_app.command("setup")
def setup_cmd(
    label: str = typer.Argument(
        ...,
        help="Account label, e.g. 'alt1', 'laptop'. Pattern [a-z0-9-]{1,40}.",
    ),
    adapter: str = typer.Option(
        "claude-code-cli",
        "--adapter",
        help=(
            "Adapter type. Default claude-code-cli — the only auth-capable "
            "adapter using setup-tokens today. Other adapters fail-fast."
        ),
    ),
    backend: str = typer.Option(
        "both",
        "--backend",
        help=(
            "Storage backend: 'local', 'gcp-secret-manager', or 'both'. "
            "Default 'both' pushes to both backends in one operation; "
            "if METAPROC_GCP_PROJECT is unset, 'both' silently degrades "
            "to local-only."
        ),
    ),
    user: str | None = typer.Option(
        None, "--user", help="Pool owner (default: $METAPROC_AUTH_POOL or $USER)."
    ),
    probe: bool = typer.Option(
        True,
        "--probe/--no-probe",
        help=(
            "Run a real-API probe against each backend after push. "
            "Default true; pass --no-probe to skip the live API call."
        ),
    ),
    account_id: str | None = typer.Option(
        None,
        "--account-id",
        help="Stable per-account hash for the classifier's quota-group walk.",
    ),
    organization_uuid: str | None = typer.Option(
        None,
        "--organization-uuid",
        help="Anthropic organizationUuid; labels in the same org share quota.",
    ),
    quota_group: str = typer.Option(
        "auto",
        "--quota-group",
        help="auto | org:UUID | account:HEX | unknown.",
    ),
    token_file: Path | None = typer.Option(
        None,
        "--token-file",
        help=(
            "Read the token from this file instead of running "
            "`claude setup-token`. Whitespace stripped."
        ),
    ),
) -> None:
    """One-step Claude Code account setup: mint → push → probe.

    The fast path. For a fresh account or a token rotation, run:

        metaproc auth setup alt1

    metaproc spawns `claude setup-token` (interactive — operator
    follows the OAuth flow in their terminal/browser), pushes the
    resulting long-lived token to the pool (both backends by default),
    and probes the live API to confirm the token actually
    authenticates. Three input modes via the same command:

        metaproc auth setup alt1                          # interactive
        claude setup-token | metaproc auth setup alt1     # piped
        metaproc auth setup alt1 --token-file <path>      # file (rare)

    Token-handling discipline:
    - Token held in-memory only — metaproc never writes it to disk.
    - Never printed to logs / stdout / stderr (the same
      never-print-payload invariant `auth push` enforces).
    - Set in the subprocess env only for the duration of each backend's
      probe call; unset on exit. The pool backends store the token as
      a credential blob — that's their job.

    Idempotent: re-running refreshes the token (new pool version).

    For codex / gemini and other adapters that don't have a Vehicle A
    setup-token equivalent, this command refuses with an actionable
    error pointing to `auth push --vehicle login-credentials`.
    """
    out = get_output()

    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    adapter_impl = _require_auth_capable(adapter)
    mint_command = adapter_impl.setup_token_command()
    if mint_command is None:
        supported = _adapters_supporting_setup()
        supported_str = ", ".join(supported) if supported else "(none)"
        out.error(
            f"adapter {adapter!r} doesn't support `auth setup` "
            f"(no setup-token mint command). "
            f"Adapters with setup-token support: {supported_str}. "
            f"For others use `auth push --vehicle login-credentials`."
        )
        raise typer.Exit(code=1)

    target_backends = _resolve_setup_backends(backend)
    token = _source_setup_token(token_file=token_file, mint_command=mint_command)

    # Token handling: scope to env only for the duration of the inner
    # push_cmd calls (which use the env-var fallback in
    # _source_oauth_token), then restore the original env. Never log.
    saved_env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    fp_seen: str | None = None
    try:
        for be in target_backends:
            out.progress(f"→ pushing {adapter}/{label} to {be}")
            push_cmd(
                adapter=adapter,
                label=label,
                vehicle="oauth-token",
                token_file=None,
                account_id=account_id,
                organization_uuid=organization_uuid,
                quota_group=quota_group,
                backend=be,
                user=user,
                probe=probe,
                _quiet=True,
            )
            # After push, re-read the entry so we can include the fp
            # in the final summary without keeping the token around.
            try:
                pool_user = _resolve_pool_user(user)
                pool_backend = BACKEND_FACTORY(be, pool_user=pool_user)
                fp_seen = pool_backend.get_entry(adapter, label).state.fp
            except KeyError:
                fp_seen = None
    finally:
        if saved_env is None:
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = saved_env
        # Best-effort: drop the local binding. Python doesn't zeroize
        # memory, but releasing the reference removes one liveness path.
        del token

    backends_str = ",".join(target_backends)
    fp_part = f"  fp={fp_seen}" if fp_seen else ""
    probe_part = "  probed" if probe else "  (no probe)"
    out.summary(
        f"✓ setup complete  {adapter}/{label}{fp_part}  backends={backends_str}{probe_part}",
        record={
            "adapter": adapter,
            "label": label,
            "backends": list(target_backends),
            "probed": probe,
            "fp": fp_seen,
            "vehicle": "claude_code_oauth_token",
        },
    )


@auth_app.command("list")
def list_cmd(
    adapter: str | None = typer.Option(None, "--adapter", help="Filter by adapter type."),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status: active | cooling | expired | disabled.",
    ),
    usage: bool = typer.Option(
        False, "--usage", help="Augment rows with query_quota_usage data (slow)."
    ),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of YAML."
    ),
) -> None:
    """List pool entries. Shows labels + state only — never the payload."""
    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    entries = pool_backend.list_entries(adapter=adapter)
    if status is not None:
        if status not in ("active", "cooling", "expired", "disabled"):
            raise typer.BadParameter(
                f"--status must be one of active|cooling|expired|disabled, got {status!r}"
            )
        entries = [e for e in entries if e.state.status == status]

    rows: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: (e.adapter, e.label)):
        row = _render_entry_row(entry)
        if usage:
            adapter_impl = get_auth_capable(entry.adapter)
            quota: Any = None
            if adapter_impl is not None:
                # query_quota_usage must be safe without a materialized
                # slot for this surface — Phase 1 adapters all return
                # None, which renders as "—".
                try:
                    with tempfile.TemporaryDirectory(prefix="mp-usage-") as slot:
                        adapter_impl.materialize_credential(Path(slot), entry.blob)
                        quota = adapter_impl.query_quota_usage(Path(slot))
                except Exception:  # noqa: BLE001 — best-effort
                    quota = None
            if quota is not None:
                if quota.remaining_units is not None and quota.total_units is not None:
                    row["remaining"] = (
                        f"{quota.remaining_units}/{quota.total_units} {quota.unit_kind}"
                    )
                else:
                    row["remaining"] = quota.detail or "—"
                row["resets_at"] = _format_ts(quota.resets_at)
            else:
                row["remaining"] = "—"
                row["resets_at"] = "—"
        rows.append(row)

    payload = {"entries": rows}
    if as_json:
        import json as _json  # noqa: PLC0415 -- pre-existing local import; needs review

        out.data(_json.dumps(payload, indent=2, default=str))
    else:
        out.data(payload)


@auth_app.command("enable")
def enable_cmd(
    adapter: str = typer.Option(..., "--adapter"),
    label: str = typer.Option(..., "--label"),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
) -> None:
    """Flip a ``disabled`` entry back to ``active``.

    Refuses ``expired`` entries — those need a fresh ``auth push`` from
    an operator-side re-auth (``claude login``). Refuses entries that
    are already ``active`` so the CLI is operator-informative rather
    than silently a no-op.
    """
    out = get_output()
    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    pool_user = _resolve_pool_user(user)
    pool_backend = BACKEND_FACTORY(_resolve_backend_name(backend), pool_user=pool_user)
    try:
        entry = pool_backend.get_entry(adapter, label)
    except KeyError as exc:
        out.error(exc.args[0] if exc.args else str(exc))
        raise typer.Exit(code=1) from exc
    if entry.state.status == "expired":
        out.error(
            f"{adapter}/{label} is expired; run `metaproc auth push --adapter "
            f"{adapter} --label {label}` with a fresh credential."
        )
        raise typer.Exit(code=1)
    if entry.state.status == "active":
        out.error(f"{adapter}/{label} is already active; nothing to do.")
        raise typer.Exit(code=1)

    new_state = _replace(entry.state, status="active", last_ok_ts=int(time.time()))
    pool_backend.upsert_entry(
        adapter,
        label,
        blob=None,
        state=new_state,
        expected_etag=entry.etag,
    )
    out.summary(
        f"✓ enabled {adapter}/{label}  status=active",
        record={"adapter": adapter, "label": label, "status": "active"},
    )


@auth_app.command("disable")
def disable_cmd(
    adapter: str = typer.Option(..., "--adapter"),
    label: str = typer.Option(..., "--label"),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
) -> None:
    """Mark an entry ``disabled``. Pool will never select it until re-enabled."""
    out = get_output()
    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    pool_user = _resolve_pool_user(user)
    pool_backend = BACKEND_FACTORY(_resolve_backend_name(backend), pool_user=pool_user)
    try:
        entry = pool_backend.get_entry(adapter, label)
    except KeyError as exc:
        out.error(exc.args[0] if exc.args else str(exc))
        raise typer.Exit(code=1) from exc

    new_state = _replace(entry.state, status="disabled")
    pool_backend.upsert_entry(
        adapter,
        label,
        blob=None,
        state=new_state,
        expected_etag=entry.etag,
    )
    out.summary(
        f"✓ disabled {adapter}/{label}",
        record={"adapter": adapter, "label": label, "status": "disabled"},
    )


@auth_app.command("check")
def check_cmd(
    adapter: str = typer.Option(..., "--adapter"),
    label: str = typer.Option(..., "--label"),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
) -> None:
    """Probe a pool entry against a transient slot and update its state.

    Materializes the pool's current blob into a throwaway slot dir,
    runs the adapter's CLI-driven live probe through ``check_auth``
    (same surface ``metaproc auth-check --live`` uses), and flips
    the label to ``active`` on success or leaves it as-is on failure.
    Never mutates the operator's real ``~/.claude/`` or ``~/.codex/``.

    Phase 2 scope (OQ12): reuses the CLI-driven probe. A direct
    OAuth-refresh HTTP path lands later if the CLI spawn cost
    dominates ``auth check --all`` latency at >20 labels.
    """

    out = get_output()
    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    adapter_impl = _require_auth_capable(adapter)
    pool_user = _resolve_pool_user(user)
    pool_backend = BACKEND_FACTORY(_resolve_backend_name(backend), pool_user=pool_user)
    try:
        entry = pool_backend.get_entry(adapter, label)
    except KeyError as exc:
        out.error(exc.args[0] if exc.args else str(exc))
        raise typer.Exit(code=1) from exc

    # Materialize into a transient slot dir and run the adapter's
    # auth_check surface. We tear down the slot dir regardless.
    with tempfile.TemporaryDirectory(prefix="mp-check-") as slot_str:
        slot_dir = Path(slot_str)
        try:
            adapter_impl.materialize_credential(slot_dir, entry.blob)
        except RuntimeError as exc:
            out.error(f"materialize failed: {exc}")
            raise typer.Exit(code=1) from exc

        from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            auth_override_refusal_keys,
        )
        from metaproc.dispatch.slot_coordinator import (  # noqa: PLC0415 -- pre-existing local import; needs review
            apply_slot_env,
        )

        # Run check_auth under the same slot scope/scrub env used by
        # dispatch. This keeps the probe tied to the materialized pool
        # entry instead of the operator's ambient ~/.claude, ~/.codex,
        # or API-key environment.
        scrub_env = {
            **adapter_impl.credential_scrub_env(),
            **{key: "" for key in auth_override_refusal_keys(adapter)},
        }
        probe_env = apply_slot_env(
            dict(os.environ),
            scope_env=adapter_impl.credential_scope_env(slot_dir),
            scrub_env=scrub_env,
        )
        with _temporary_environ(probe_env):
            status = adapter_impl.check_auth()

    # Adapter returns an AuthStatus — map credentials_found/auth_mode
    # into an operator-facing line. No pool state flip yet; that
    # requires a stronger signal (refresh actually succeeded) which
    # we plan to add in the follow-up direct-OAuth path (OQ12 (b)).
    result = {
        "adapter": adapter,
        "label": label,
        "user": pool_user,
        "status": entry.state.status,
        "credentials_found": status.credentials_found,
        "auth_mode": status.auth_mode,
        "details": status.details,
    }
    if not status.credentials_found:
        out.error(f"check failed for {adapter}/{label}: {status.details}")
        out.summary(
            f"✗ check failed  {adapter}/{label}  status={result['status']}  "
            f"auth_mode={status.auth_mode or 'none'}",
            record=result,
        )
        raise typer.Exit(code=1)

    # Materialized blob passed the adapter's local check — flip
    # cooling/disabled back to active (not expired; expired needs a
    # fresh push). Intentionally conservative: we don't touch active
    # labels either so a repeated check never churns the pool.
    if entry.state.status in ("cooling", "disabled"):
        new_state = state_mark_ok(entry.state)
        pool_backend.upsert_entry(
            adapter,
            label,
            blob=None,
            state=new_state,
            expected_etag=entry.etag,
        )
        result["status"] = "active"
    out.summary(
        f"✓ check passed  {adapter}/{label}  status={result['status']}  "
        f"auth_mode={status.auth_mode or 'unknown'}",
        record=result,
    )


@_dataclass(frozen=True)
class _LiveProbeOutcome:
    """Result of a single live probe against a (adapter, label) pair.

    Trimmed shape of what ``probe_cmd`` returns, plus the parsed
    state literal so callers (notably ``auth doctor --probe``) can
    populate ``LabelUsage.probe_state`` without re-parsing. Probe
    side-effects (pool state writeback) happen inside the helper —
    this struct is the post-probe summary.
    """

    state: str  # active | cooling | expired | unknown
    request_id: str
    api_status: int | None
    note: str


def _probe_label_state(
    adapter_impl: AuthCapableCliAdapter,
    pool_backend: PoolBackend,
    adapter: str,
    label: str,
) -> _LiveProbeOutcome | None:
    """Run a single live probe against ``(adapter, label)`` and return
    the parsed outcome (or ``None`` if the entry is missing).

    Extracted from :func:`probe_cmd` (upstream change review P2): the same
    materialize-slot + ``claude --print`` + parse-debug-log flow,
    but as a library-call shape that ``auth doctor --probe`` can
    invoke per label without going through Typer.

    Pool state writeback runs as a side effect (matching ``probe_cmd``
    semantics), so a stale ``cooling`` mark gets corrected when a
    real probe shows ``active``.
    """

    try:
        entry = pool_backend.get_entry(adapter, label)
    except KeyError:
        return None

    with tempfile.TemporaryDirectory(prefix="mp-probe-") as slot_str:
        slot_dir = Path(slot_str)
        entry_vehicle = entry.state.vehicle
        try:
            adapter_impl.materialize_credential(slot_dir, entry.blob, vehicle=entry_vehicle)
        except RuntimeError:
            # Materialize failure looks like an expired credential
            # from the operator's perspective; surface as such.
            return _LiveProbeOutcome(
                state="expired",
                request_id="",
                api_status=None,
                note="materialize failed",
            )

        from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            auth_override_refusal_keys,
        )
        from metaproc.dispatch.slot_coordinator import (  # noqa: PLC0415 -- pre-existing local import; needs review
            apply_slot_env,
        )

        scrub_env = {
            **adapter_impl.credential_scrub_env(vehicle=entry_vehicle),
            **{key: "" for key in auth_override_refusal_keys(adapter)},
        }
        probe_env = apply_slot_env(
            dict(os.environ),
            scope_env=adapter_impl.credential_scope_env(
                slot_dir, vehicle=entry_vehicle, blob=entry.blob
            ),
            scrub_env=scrub_env,
        )
        debug_args = list(adapter_impl.debug_capture_args(slot_dir))
        cmd = ["claude", *debug_args, "--print", "OK"]
        try:
            proc = subprocess.run(
                cmd,
                env=probe_env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return _LiveProbeOutcome(
                state="unknown",
                request_id="",
                api_status=None,
                note=f"probe failed: {exc.__class__.__name__}",
            )
        debug_log_path = slot_dir / "claude-code-debug.log"
        debug_text = ""
        if debug_log_path.exists():
            try:
                debug_text = debug_log_path.read_text()
            except OSError:
                debug_text = ""

    parsed = parse_claude_api_signals(debug_text=debug_text, stdout=proc.stdout)
    transition = _classify_probe_result(parsed=parsed, exit_code=proc.returncode)
    _apply_probe_state_transition(
        pool_backend=pool_backend,
        adapter=adapter,
        label=label,
        entry=entry,
        target_status=transition.target_status,
    )
    return _LiveProbeOutcome(
        state=transition.target_status,
        request_id=parsed.request_id or "",
        api_status=parsed.api_status,
        note=transition.note,
    )


@auth_app.command("probe")
def probe_cmd(
    adapter: str = typer.Option(..., "--adapter"),
    label: str = typer.Option(..., "--label"),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
    show_headers: bool = typer.Option(
        False,
        "--headers",
        help=(
            "Print the parsed Anthropic response details (status, request_id, "
            "OAuth refresh boundary). Headers proper require a follow-up direct-httpx "
            "path; for now this surfaces what `claude -d api` exposes."
        ),
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of YAML."
    ),
    _quiet: bool = False,
) -> None:
    """Real-API probe of a pool credential.

    Probes the credential stored in the pool blob — NOT whatever
    ``claude /login`` last wrote to ``~/.claude`` or the macOS Keychain.
    If the operator just re-authed interactively but didn't run
    ``metaproc auth push --label <X>``, the pool blob is still stale
    and this probe will report the stale state; that's correct (it
    reflects what dispatch will see). To re-test against fresh tokens
    after re-auth, run ``auth push`` first.

    Materializes the slot the way dispatch does, then runs ``claude -d api
    --debug-file=<tmp>/probe.log --print "OK"`` against it. Parses the debug
    log for OAuth refresh status, per-attempt API status, ``request_id``, and
    the actual error excerpt. Maps the result to an authoritative pool-state
    transition: ``200 → active``, ``401 / OAuth refresh 400 → expired``,
    ``429 → cooling`` (with retry-after when present in the body).

    Distinct from ``auth check``, which is a structural-only probe (verifies
    the credential file is parseable but never makes an API call). Use ``probe``
    when the question is "is this credential actually working right now?".

    The output's ``source`` field names the credential under test
    (backend + fingerprint) so operators don't conflate the pool's view
    with their ambient session.
    """

    out = get_output()
    try:
        validate_operator_label(label)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    adapter_impl = _require_auth_capable(adapter)
    pool_user = _resolve_pool_user(user)
    pool_backend = BACKEND_FACTORY(_resolve_backend_name(backend), pool_user=pool_user)
    try:
        entry = pool_backend.get_entry(adapter, label)
    except KeyError as exc:
        out.error(exc.args[0] if exc.args else str(exc))
        raise typer.Exit(code=1) from exc

    with tempfile.TemporaryDirectory(prefix="mp-probe-") as slot_str:
        slot_dir = Path(slot_str)
        # Vehicle-aware materialization: Vehicle A writes nothing into
        # the slot (the bearer token is injected via scope_env), Vehicle B
        # writes <slot>/.credentials.json mode 0600.
        entry_vehicle = entry.state.vehicle
        try:
            adapter_impl.materialize_credential(slot_dir, entry.blob, vehicle=entry_vehicle)
        except RuntimeError as exc:
            out.error(f"materialize failed: {exc}")
            raise typer.Exit(code=1) from exc

        from metaproc.dispatch.pool_dispatch import (  # noqa: PLC0415 -- pre-existing local import; needs review
            auth_override_refusal_keys,
        )
        from metaproc.dispatch.slot_coordinator import (  # noqa: PLC0415 -- pre-existing local import; needs review
            apply_slot_env,
        )

        scrub_env = {
            **adapter_impl.credential_scrub_env(vehicle=entry_vehicle),
            **{key: "" for key in auth_override_refusal_keys(adapter)},
        }
        probe_env = apply_slot_env(
            dict(os.environ),
            scope_env=adapter_impl.credential_scope_env(
                slot_dir, vehicle=entry_vehicle, blob=entry.blob
            ),
            scrub_env=scrub_env,
        )

        # Use the adapter's debug-capture flags so the probe sees the
        # same API-level output the dispatch path captures.
        debug_args = list(adapter_impl.debug_capture_args(slot_dir))
        cmd = ["claude", *debug_args, "--print", "OK"]

        proc = subprocess.run(
            cmd,
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        debug_log_path = slot_dir / "claude-code-debug.log"
        debug_text = ""
        if debug_log_path.exists():
            try:
                debug_text = debug_log_path.read_text()
            except OSError:
                debug_text = ""

    parsed = parse_claude_api_signals(debug_text=debug_text, stdout=proc.stdout)
    transition = _classify_probe_result(parsed=parsed, exit_code=proc.returncode)

    new_state = _apply_probe_state_transition(
        pool_backend=pool_backend,
        adapter=adapter,
        label=label,
        entry=entry,
        target_status=transition.target_status,
    )

    result = {
        "adapter": adapter,
        "label": label,
        "vehicle": entry.state.vehicle.value,
        "account_id": entry.state.account_id,
        "organization_uuid": entry.state.organization_uuid,
        "quota_group": _format_quota_group(entry.state.quota_group),
        "user": pool_user,
        # Probes the credential stored in the pool blob — not whatever
        # `claude /login` last wrote to ~/.claude or the macOS Keychain.
        # If the operator just re-auth'd interactively but did not run
        # `metaproc auth push`, this fingerprint is the stale one and
        # the probe correctly reports the pool's view, not the ambient
        # session's view.
        "source": f"pool blob (backend={_resolve_backend_name(backend)}, fp={entry.state.fp})",
        "fp": entry.state.fp,
        "exit_code": proc.returncode,
        "result": transition.target_status,  # active | expired | cooling | unknown
        "api_status": parsed.api_status,
        "oauth_refresh_status": parsed.oauth_refresh_status,
        "request_id": parsed.request_id,
        "error_excerpt": parsed.error_body_excerpt[:300] if parsed.error_body_excerpt else "",
        "retry_after_s": parsed.retry_after_s,
        "pool_status_before": entry.state.status,
        "pool_status_after": new_state.status if new_state is not None else entry.state.status,
        "note": transition.note,
    }
    if show_headers:
        result["headers_note"] = (
            "claude -d api debug log does not expose response headers; "
            "anthropic-ratelimit-* fields require a direct-httpx probe "
            "(future follow-up: direct-httpx probe)."
        )

    # TEXT mode emits a one-line scannable summary; --json forces a
    # full JSON record on stdout (operator opt-in for machine reads);
    # YAML / JSON output formats route through the structured side
    # of summary(). When _quiet (called from auth setup), suppress
    # entirely so the wrapping command can emit one final line.
    if not _quiet:
        if as_json:
            out.data(_json.dumps(result, indent=2, default=str))
        else:
            symbol = "✓" if transition.target_status in ("active", "cooling") else "✗"
            api_part = f"  api={parsed.api_status}" if parsed.api_status is not None else ""
            rid_part = f"  rid={parsed.request_id[:8]}…" if parsed.request_id else ""
            out.summary(
                f"{symbol} probe {transition.target_status}  "
                f"{adapter}/{label}  fp={entry.state.fp}{api_part}{rid_part}",
                record=result,
            )
    if transition.target_status not in ("active", "cooling"):
        raise typer.Exit(code=1)


@auth_app.command("rotate")
def rotate_cmd(
    adapter: str = typer.Option(..., "--adapter"),
    label: str = typer.Option(..., "--label"),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
) -> None:
    """Re-read the native credential and push a new version.

    Same semantics as ``push`` but explicit about intent so playbooks
    and audit logs can distinguish "first push" from "operator-driven
    rotation after claude login renewal". Per-label — does not touch
    siblings. Always rotates as Vehicle B (re-reads native source);
    operators rotating to Vehicle A use ``auth push --vehicle oauth-token``
    explicitly.
    """
    # Forward every kwarg explicitly: typer.OptionInfo defaults on
    # push_cmd's signature don't unwrap when push_cmd is called from
    # Python rather than via CLI parsing, so omitting any kwarg leaves
    # the parameter bound to a typer.OptionInfo marker (which then
    # rejects the BadParameter check downstream).
    # Always rotate as Vehicle B — re-reading the native source is the
    # operator-driven Keychain refresh path. Operators rotating Vehicle A
    # tokens run `auth push --vehicle oauth-token --token-file <new>`
    # directly (since "rotate" for V-A means "mint a new setup-token and
    # push it" — not a re-read of any local source).
    push_cmd(
        adapter=adapter,
        label=label,
        vehicle="login-credentials",
        token_file=None,
        account_id=None,
        organization_uuid=None,
        quota_group="auto",
        backend=backend,
        user=user,
        probe=False,
    )


@auth_app.command("prune")
def prune_cmd(
    adapter: str | None = typer.Option(None, "--adapter"),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Only prune entries in this status (expired|disabled|cooling).",
    ),
    older_than_days: int | None = typer.Option(
        None,
        "--older-than-days",
        help="Only prune entries whose last_ok_ts is older than this many days.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm destructive prune. Without --yes this is a dry-run.",
    ),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
) -> None:
    """Delete pool entries matching the filters.

    Dry-run by default: prints what would be deleted and exits 0 without
    mutating state. Pass ``--yes`` to actually delete. Filters stack —
    ``--status expired --older-than-days 30`` matches entries that are
    both expired AND haven't seen a successful run in 30 days.

    Safety rail: at least one of ``--adapter`` / ``--status`` /
    ``--older-than-days`` must be supplied; bare ``auth prune`` with
    no filters would wipe the entire pool, which is almost never what
    the operator wants.
    """
    out = get_output()
    if adapter is None and status is None and older_than_days is None:
        raise typer.BadParameter(
            "auth prune requires at least one filter "
            "(--adapter / --status / --older-than-days). "
            "Refusing to wipe the whole pool."
        )
    if status is not None and status not in ("active", "cooling", "expired", "disabled"):
        raise typer.BadParameter(
            f"--status must be one of active|cooling|expired|disabled, got {status!r}"
        )
    pool_user = _resolve_pool_user(user)
    pool_backend = BACKEND_FACTORY(_resolve_backend_name(backend), pool_user=pool_user)

    entries = pool_backend.list_entries(adapter=adapter)
    cutoff_ts: int | None = None
    if older_than_days is not None:
        cutoff_ts = int(time.time()) - older_than_days * 86_400
    to_prune: list[tuple[str, str]] = []
    active_pruned: list[tuple[str, str]] = []
    for entry in entries:
        if status is not None and entry.state.status != status:
            continue
        if cutoff_ts is not None:
            last = entry.state.last_ok_ts
            # Entries without a last_ok_ts are treated as ancient
            # (never succeeded) — prune-eligible when --older-than is
            # set so stale never-used labels don't linger.
            if last is not None and last >= cutoff_ts:
                continue
        to_prune.append((entry.adapter, entry.label))
        if entry.state.status == "active":
            active_pruned.append((entry.adapter, entry.label))

    # Render entries as "adapter/label" strings — far more readable
    # than the raw tuple-of-tuples shape that ruamel YAML-dumps as
    # nested-list-of-list. Structured (JSON / YAML) consumers still
    # get the same shape via the record kwarg below.
    def _ids(pairs: list[tuple[str, str]]) -> list[str]:
        return [f"{a}/{lbl}" for a, lbl in pairs]

    record = {
        "dry_run": not yes,
        "pruned": _ids(to_prune) if yes else [],
        "would_prune": [] if yes else _ids(to_prune),
        "count": len(to_prune),
        "active_in_set": len(active_pruned),
    }
    # Surface the active-entries-in-prune-set hazard early — `--adapter`
    # alone matches every entry for that adapter regardless of status.
    # Operators wanting "clean expired only" should add `--status expired`;
    # operators decommissioning an adapter wholesale see this as a final
    # gate before passing `--yes`.
    if active_pruned:
        out.warning(
            f"prune set includes {len(active_pruned)} ACTIVE entry(ies): "
            f"{', '.join(f'{a}/{lbl}' for a, lbl in active_pruned)}. "
            "Add --status to narrow, or confirm with --yes to delete anyway."
        )
    if not yes:
        ids_str = ", ".join(_ids(to_prune)) if to_prune else "(none)"
        out.summary(
            f"dry-run: would prune {len(to_prune)} — {ids_str}. Re-run with --yes to delete.",
            record=record,
        )
        return
    for a, lbl in to_prune:
        pool_backend.delete_entry(a, lbl)
    if not to_prune:
        out.summary("✓ prune: nothing matched the filters", record=record)
    else:
        ids_str = ", ".join(_ids(to_prune))
        out.summary(f"✓ pruned {len(to_prune)}: {ids_str}", record=record)


# ── status (P4.1 — at-a-glance pool dashboard) ─────────────────


@auth_app.command("status")
def status_cmd(
    adapter: str | None = typer.Option(None, "--adapter", help="Filter to a single adapter."),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of the text table."
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Suppress ANSI coloring even when stdout is a TTY."
    ),
) -> None:
    """At-a-glance pool readiness — which labels work, which are throttled.

    Pure state read — no provider probing, no slot materialization.
    Safe to run in a tight loop or ``watch``. For a live probe that
    actually calls each adapter's ``query_quota_usage``, use
    ``metaproc auth preflight`` (plan P4.2 — follow-up).

    Output shape (per adapter):

        CLAUDE-CODE-CLI (2/2 eligible)
          laptop   active   last_ok 2m ago
          home     active   last_ok 47m ago
          earliest_reset: —

    The header line answers "across my N accounts, how many can
    dispatch right now?". The earliest_reset line answers "when does
    the first throttled label come back?".
    """

    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    report = build_pool_status(
        pool_backend,
        pool_user=pool_user,
        backend_name=backend_name,
        adapter=adapter,
    )

    if as_json:
        # Serialize the dict explicitly so TEXT mode (the default)
        # still emits valid JSON. OutputManager's TEXT mode writes
        # str(obj) which produces Python repr (single quotes), not
        # JSON — always going through json.dumps keeps the contract
        # stable for automation / agent callers.
        out.data(_json.dumps(to_json_dict(report), indent=2, default=str))
        return
    use_color = sys.stdout.isatty() and not no_color
    out.data(render_text(report, use_color=use_color))


# ── Phase 2: aggregator-backed observability commands ──


@auth_app.command("usage")
def usage_cmd(
    run_dir: Path = typer.Argument(
        ..., help="Run directory (containing .logs/runpool/.../events.jsonl streams)"
    ),
    adapter: str | None = typer.Option(
        None, "--adapter", help="Filter to a single adapter (e.g. claude-code-cli)."
    ),
    backend: str | None = typer.Option(
        None,
        "--backend",
        help="Pool backend (local|gcp-secret-manager). Defaults per the same rules as auth status.",
    ),
    user: str | None = typer.Option(None, "--user"),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text|json|yaml. Defaults to text.",
    ),
) -> None:
    """Per-label deep-dive on a single run's auth-pool activity.

    Combines five on-disk sources into one operator-readable view:

    - Pool state file (active / cooling / expired).
    - Runpool ``events.jsonl`` ``auth_outcome`` aggregation (per-label invocation
      counts, ok/fail split, known-bug distribution).
    - ``auth_lease_acquired`` events (schema-v2 join keys).
    - Per-session JSONL ``rate_limit_event`` records (5h / 7d
      utilization + resetsAt).
    - The dispatch's run-config snapshot, when available.

    Spec: plan-2026-05-03-auth-observability-and-load-balancing.md
    § metaproc auth usage.
    """

    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    usage_by_label = aggregate_label_usage_for_run(run_dir, backend=pool_backend, adapter=adapter)

    # Load run-config.yaml for the snapshot block. Phase 3
    # added the auth: + concurrency: blocks — we render those plus any
    # legacy flat fields so older dispatches still surface something.
    config: dict[str, Any] | None = None
    config_path = paths_mod.run_config_file(run_dir)
    if config_path.exists():
        try:
            from metaproc.io import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
                read_yaml_file,
            )

            raw = read_yaml_file(config_path)
            if isinstance(raw, dict):
                config = {
                    k: v
                    for k, v in raw.items()
                    if k
                    in (
                        "auth",
                        "concurrency",
                        "auth_account",
                        "auth_include_labels",
                        "auth_fallback_policy",
                        "auth_policy",
                        "max_concurrency",
                    )
                }
        except Exception:  # noqa: BLE001 — best-effort snapshot
            log.warning("auth usage: failed to read %s; rendering without config", config_path)
    # Append the resume timeline (dispatch_config_change events) so the
    # operator sees what changed on each --from / --only resume.
    changes_path = paths_mod.dispatch_config_changes_for_read(run_dir)
    if changes_path.is_file():
        try:
            timeline: list[dict[str, Any]] = []
            timeline.extend(iter_jsonl_objects(changes_path))
            if timeline:
                if config is None:
                    config = {}
                config["resume_timeline"] = timeline
        except OSError:
            pass

    if output_format == "json":
        report = usage_report_to_dict(
            run_dir=str(run_dir), usage_by_label=usage_by_label, config=config
        )
        out.data(_json.dumps(report, indent=2, default=str))
        return
    if output_format == "yaml":
        report = usage_report_to_dict(
            run_dir=str(run_dir), usage_by_label=usage_by_label, config=config
        )
        out.data(to_yaml_string(report))
        return
    if output_format != "text":
        raise typer.BadParameter(f"--format must be one of text|json|yaml, got {output_format!r}")
    out.data(
        render_run_usage_text(run_dir=str(run_dir), usage_by_label=usage_by_label, config=config)
    )


@auth_app.command("doctor")
def doctor_cmd(
    run_dir: Path | None = typer.Option(
        None,
        "--run-dir",
        help="Aggregate runpool event streams under this run-dir before checking consistency. Without this flag, doctor only inspects pool state.",
    ),
    adapter: str | None = typer.Option(None, "--adapter", help="Filter to a single adapter."),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
    probe: bool = typer.Option(
        False,
        "--probe",
        help=(
            "Issue a live ``claude --print`` probe per label in scope. Off by "
            "default — probes consume rate-limit budget. When set, the doctor "
            "report's per-label ``probe`` block is populated and pool state is "
            "transitioned (cooling→active recovery, etc.) per the probe outcome."
        ),
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text|json. Defaults to text.",
    ),
) -> None:
    """Cross-source consistency check on the credential pool.

    Reconciles four sources (per spec § metaproc auth doctor): pool
    state file, latest rate-limit headroom from per-session JSONLs,
    runpool event aggregations, and (when ``--probe`` is set) a
    live API verification call.

    **Doctor never auto-probes.** When an inconsistency would be
    resolved by a probe (e.g. pool state says ACTIVE but JSONL shows
    a recent 429), doctor flags it and tells the operator to re-run
    with ``--probe``. This avoids silently consuming rate-limit
    budget during a live dispatch.

    Without ``--run-dir``, doctor only inspects pool state plus any
    recent rate-limit events it can find under ``RUNS_DIR``. With
    ``--run-dir``, the per-run aggregator runs and the consistency
    rules see the full join-key attribution.

    Spec: plan-2026-05-03-auth-observability-and-load-balancing.md.
    """

    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    if run_dir is not None:
        usage_by_label = aggregate_label_usage_for_run(
            run_dir, backend=pool_backend, adapter=adapter
        )
    else:
        # Pool-state-only mode: aggregate over an empty events dir so
        # the aggregator just surfaces pool state without any
        # per-run attribution.
        from tempfile import (  # noqa: PLC0415 -- pre-existing local import; needs review
            TemporaryDirectory,
        )

        with TemporaryDirectory() as td:
            usage_by_label = aggregate_label_usage_for_run(
                Path(td), backend=pool_backend, adapter=adapter
            )

    if probe:
        # Live-probe loop: materialize a slot per label and run a
        # 1-token ``claude --print "OK"`` to verify pool state. Costs
        # rate-limit budget — operator opt-in only. Reuses the helper
        # extracted from probe_cmd so doctor and probe share one
        # implementation (upstream change review P2: this used to be a no-op
        # despite an operator-facing flag).
        from dataclasses import (  # noqa: PLC0415 -- pre-existing local import; needs review
            replace as _replace,
        )

        probed_usage: dict[tuple[str, str], Any] = {}
        for key, u in usage_by_label.items():
            adp, lbl = key
            adapter_impl = get_auth_capable(adp)
            if adapter_impl is None:
                probed_usage[key] = u
                continue
            outcome = _probe_label_state(adapter_impl, pool_backend, adp, lbl)
            if outcome is None:
                probed_usage[key] = u
                continue
            # Probe states are a subset of LabelUsage.probe_state's
            # Literal — tolerate the rare ``unknown`` by casting,
            # since the literal narrows to the known transitions.
            probed_usage[key] = _replace(
                u,
                probe_state=outcome.state
                if outcome.state in ("active", "cooling", "expired")
                else "unknown",
                probe_request_id=outcome.request_id or None,
                probe_age_s=0,
            )
        usage_by_label = probed_usage

    if output_format == "json":
        report = doctor_report_to_dict(usage_by_label=usage_by_label, probe_requested=probe)
        out.data(_json.dumps(report, indent=2, default=str))
        return
    if output_format != "text":
        raise typer.BadParameter(f"--format must be one of text|json, got {output_format!r}")
    out.data(render_doctor_text(usage_by_label=usage_by_label, probe_requested=probe))


@auth_app.command("quota")
def quota_cmd(
    adapter: str | None = typer.Option(  # noqa: UP007
        None,
        "--adapter",
        help="Filter to one adapter (e.g. claude-code-cli).",
    ),
    label: str | None = typer.Option(  # noqa: UP007
        None,
        "--label",
        help="Query a single label rather than every pool entry.",
    ),
    backend: str | None = typer.Option(None, "--backend"),  # noqa: UP007
    user: str | None = typer.Option(None, "--user"),  # noqa: UP007
    include_disabled: bool = typer.Option(
        False,
        "--include-disabled",
        help="Include entries with state.status='disabled'.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text|json. Defaults to text.",
    ),
) -> None:
    """Live per-account quota readout from each pool credential.

    Hits the vendor's quota HUD endpoint directly (Claude Code:
    ``GET /api/oauth/usage``) so headroom is visible **before** the
    first 429 of a run. Falls back to silent ``—`` for adapters or
    accounts that don't expose a live endpoint; the existing
    ``--usage`` flag on ``auth list`` still uses the derived path.

    Pre-dispatch use:

    .. code-block:: text

        $ uv run metaproc auth quota --adapter claude-code-cli --format json
        {"entries": [{"adapter":"claude-code-cli","label":"alt1",
          "remaining_pct": 67, "resets_at": "2026-05-13T03:10:00-07:00",
          "binding_window": "five_hour", "detail": "..."}]}
    """

    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    entries = pool_backend.list_entries(adapter=adapter)
    if label is not None:
        entries = [e for e in entries if e.label == label]
    if not include_disabled:
        entries = [e for e in entries if e.state.status != "disabled"]

    rows: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: (e.adapter, e.label)):
        adapter_impl = get_auth_capable(entry.adapter)
        quota: Any = None
        if adapter_impl is not None:
            try:
                with tempfile.TemporaryDirectory(prefix="mp-quota-") as slot:
                    slot_dir = Path(slot) / entry.label
                    entry_vehicle = entry.state.vehicle
                    adapter_impl.materialize_credential(
                        slot_dir,
                        entry.blob,
                        vehicle=entry_vehicle,
                    )
                    quota = adapter_impl.query_live_quota(
                        slot_dir,
                        vehicle=entry_vehicle,
                        blob=entry.blob,
                    )
            except Exception:  # noqa: BLE001 — quota probe is best-effort
                quota = None
        row: dict[str, Any] = {
            "adapter": entry.adapter,
            "label": entry.label,
            "status": entry.state.status,
        }
        if quota is None:
            row["remaining_pct"] = None
            row["resets_at"] = None
            row["binding_window"] = None
            row["detail"] = "—"
        else:
            remaining = quota.remaining_ratio
            row["remaining_pct"] = int(round(remaining * 100)) if remaining is not None else None
            row["resets_at"] = _format_ts(quota.resets_at)
            row["binding_window"] = quota.unit_kind
            row["detail"] = quota.detail or "—"
        rows.append(row)

    if output_format == "json":
        out.data(_json.dumps({"entries": rows}, indent=2, default=str))
        return
    if output_format != "text":
        raise typer.BadParameter(f"--format must be one of text|json, got {output_format!r}")
    if not rows:
        out.data("(no pool entries matched)\n")
        return
    lines = [f"{'adapter':24} {'label':20} {'remaining':>10} {'resets_at':24} detail"]
    for r in rows:
        pct = "—" if r["remaining_pct"] is None else f"{r['remaining_pct']}%"
        lines.append(
            f"{r['adapter']:24} {r['label']:20} {pct:>10} {(r['resets_at'] or '—'):24} {r['detail']}"
        )
    out.data("\n".join(lines) + "\n")


@auth_app.command("preflight")
def preflight_cmd(
    adapter: str = typer.Option(
        "claude-code-cli",
        "--adapter",
        help="Adapter to estimate against. Defaults to claude-code-cli.",
    ),
    estimated_items: int = typer.Option(
        ...,
        "--estimated-items",
        help="How many items the next dispatch will fan out — used to compute projected demand.",
    ),
    per_item_tokens: int | None = typer.Option(
        None,
        "--per-item-tokens",
        help=(
            "Override the per-item token estimate. Defaults to the per-adapter "
            "constant (CLAUDE_CODE_CLI_DEFAULT_TOKENS_PER_ITEM)."
        ),
    ),
    backend: str | None = typer.Option(None, "--backend"),
    user: str | None = typer.Option(None, "--user"),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text|json. Defaults to text.",
    ),
) -> None:
    """Headroom + capacity preflight against the auth pool.

    Wraps :func:`metaproc.dispatch.preflight.summarize_headroom` /
    :func:`check_step_preflight` so operators can ask "can I dispatch
    N items right now?" before kicking off the run. Uses the synthesized
    ``QuotaUsage`` synthesized by
    ``claude_code.query_quota_usage`` from recent
    rate_limit_event records under $RUNS_DIR).

    Aligns with the future-command reference in
    ``commands/auth.py:1534``'s docstring of ``auth status``. Spec:
    plan-2026-05-03-auth-observability-and-load-balancing.md
    § metaproc auth preflight.
    """

    out = get_output()
    pool_user = _resolve_pool_user(user)
    backend_name = _resolve_backend_name(backend)
    pool_backend = BACKEND_FACTORY(backend_name, pool_user=pool_user)

    verdict = check_step_preflight(
        pool_backend,
        adapter=adapter,
        step_id="auth-preflight-cli",
        fan_out_size=estimated_items,
        mean_cost_per_item=per_item_tokens,
        posture="warn",
    )

    if output_format == "json":
        report = {
            "adapter": adapter,
            "estimated_items": estimated_items,
            "verdict": verdict.status,
            "projected_units": verdict.projected_units,
            "headroom": {
                "pool_remaining_units": verdict.headroom.pool_remaining_units,
                "pool_total_units": verdict.headroom.pool_total_units,
                "earliest_reset_ts": verdict.headroom.earliest_reset_ts,
                "eligible_label_count": verdict.headroom.eligible_label_count,
            },
            "message": verdict.message,
        }
        out.data(_json.dumps(report, indent=2, default=str))
        return
    if output_format != "text":
        raise typer.BadParameter(f"--format must be one of text|json, got {output_format!r}")
    out.data(verdict.message)


@auth_app.command("env")
def env_cmd(
    process: Path = typer.Argument(
        ...,
        help="Process spec path (e.g. example_plugin/process/predict/predict.process.md).",
    ),
    posture: str = typer.Option(
        "refuse",
        "--posture",
        help=(
            "Verdict posture: off|warn|refuse. Defaults to refuse so an "
            "operator running this command sees the same exit-code semantics "
            "they'd get from a dispatch with --auth-preflight-quota-guard refuse."
        ),
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text|json. Defaults to text.",
    ),
) -> None:
    """Check the operator shell env against the process's adapter set.

    Loads *process*, collects the union of adapters its steps will use,
    and inspects ``os.environ`` for each adapter's ``auth_override_refusal_keys``
    (the same keys the per-item :func:`compose_slot_env` check refuses on, and
    the dispatch-start :func:`check_dispatch_auth_env` gate consults). Returns
    a per-adapter verdict so an operator can diagnose ambient-key conflicts
    before launching a dispatch.

    Posture semantics mirror :func:`check_dispatch_auth_env`:

    - ``off``: returns ``go`` without inspecting env (useful for output-format
      smoke tests).
    - ``warn``: returns ``warn`` with conflicting keys named; exit 0.
    - ``refuse``: returns ``refuse`` when any conflict is found; exit 1 so the
      caller can use it as a shell-script gate.

    Plan-2026-05-03 § Phase 5 this regression. Promotes the dispatch-start
    sweep (this regression) to a standalone operator tool.
    """
    out = get_output()

    try:
        validate_guard_posture(posture)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    guard_posture = cast(GuardPosture, posture)

    spec = load_process_spec(process)

    # Adapter resolution: per-step override falls back to the spec's
    # default_adapter_config.type. Matches build_plan.py:582-587 — that's
    # the authoritative resolution path for "which adapter will this step
    # run on at dispatch time".
    default_adapter = spec.defaults.default_adapter_config.type
    adapters_in_use: dict[str, list[str]] = {}
    for step in spec.steps:
        adapter_type = step.adapter.type if step.adapter is not None else default_adapter
        adapters_in_use.setdefault(adapter_type, []).append(step.id)

    per_adapter_verdicts = {
        adapter: check_dispatch_auth_env(adapter, posture=guard_posture)
        for adapter in sorted(adapters_in_use)
    }

    any_refuse = any(v.status == "refuse" for v in per_adapter_verdicts.values())
    any_warn = any(v.status == "warn" for v in per_adapter_verdicts.values())
    overall: str = "refuse" if any_refuse else ("warn" if any_warn else "go")

    if output_format == "json":
        report = {
            "process": str(process),
            "posture": posture,
            "overall": overall,
            "adapters": {
                adapter: {
                    "verdict": v.status,
                    "conflicting_keys": list(v.conflicting_keys),
                    "step_ids": adapters_in_use[adapter],
                    "message": v.message,
                }
                for adapter, v in per_adapter_verdicts.items()
            },
        }
        out.data(_json.dumps(report, indent=2, default=str))
        if overall == "refuse":
            raise typer.Exit(code=1)
        return
    if output_format != "text":
        raise typer.BadParameter(f"--format must be one of text|json, got {output_format!r}")

    out.data(f"process: {process}")
    out.data(f"posture: {posture}")
    out.data(f"overall: {overall}")
    out.data("")
    for adapter, verdict in per_adapter_verdicts.items():
        step_ids = adapters_in_use[adapter]
        out.data(f"  adapter={adapter}  steps={','.join(step_ids)}")
        out.data(f"    verdict: {verdict.status}")
        if verdict.conflicting_keys:
            out.data(f"    conflicting_keys: {', '.join(verdict.conflicting_keys)}")
        out.data(f"    message: {verdict.message}")
    if overall == "refuse":
        raise typer.Exit(code=1)
