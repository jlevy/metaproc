"""Codex CLI adapter (OpenAI Codex CLI, ``codex exec --json``)."""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

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
from metaproc.dispatch.known_bugs import detect_known_bug
from metaproc.settings import (
    CODEX_DEFAULT_EFFORT,
    CODEX_DEFAULT_MODEL,
    CODEX_VALID_APPROVAL_POLICIES,
    CODEX_VALID_EFFORTS,
    CODEX_VALID_MODELS,
    CODEX_VALID_SANDBOX_MODES,
)

# Env var carrying the ChatGPT-OAuth ~/.codex/auth.json payload to Batch workers.
# See docs/project/specs/active/plan-2026-04-23-codex-adapter-and-openai-pi-models.md.
CODEX_CREDS_ENV_VAR = "CODEX_CREDS_JSON"

# Minimal config.toml emitted alongside auth.json in a pool slot. The two
# knobs tell codex-cli to read credentials from the slot-local auth.json
# instead of the OS keychain and to pin the ChatGPT OAuth path rather
# than letting an ambient OPENAI_API_KEY silently take over. Kept small
# on purpose so future fields don't regress the pool's isolation guarantees.
_CODEX_SLOT_CONFIG_TOML = 'cli_auth_credentials_store = "file"\nforced_login_method = "chatgpt"\n'

log = logging.getLogger(__name__)

PINNED_CODEX_CLI_VERSION = "0.144.1"
CODEX_CLI_INSTALL_HINT = f"Install: npm install -g @openai/codex@{PINNED_CODEX_CLI_VERSION}"
# Keep a broken launcher from blocking agent dispatch indefinitely.
_CODEX_VERSION_CHECK_TIMEOUT_SECONDS = 10


class CodexCliVersionMismatch(RuntimeError):
    """Raised when the on-PATH Codex CLI is unusable or does not match the pin."""


def verify_codex_version(
    *, codex_path: str = "codex", expected: str = PINNED_CODEX_CLI_VERSION
) -> str:
    """Verify that the Codex launcher starts and matches the repository pin."""
    try:
        proc = subprocess.run(  # noqa: S603 — codex_path is operator-controlled
            [codex_path, "--version"],
            capture_output=True,
            text=True,
            timeout=_CODEX_VERSION_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = (
            f"Could not run `{codex_path} --version`: {exc}. "
            f"Expected pinned version {expected!r}; reinstall Codex or fix PATH."
        )
        raise CodexCliVersionMismatch(msg) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        msg = (
            f"`{codex_path} --version` exited {proc.returncode}: {detail!r}. "
            f"Expected pinned version {expected!r}; reinstall Codex or fix PATH."
        )
        raise CodexCliVersionMismatch(msg)

    output = proc.stdout.strip() or proc.stderr.strip()
    parts = output.split()
    actual = parts[1] if len(parts) >= 2 and parts[0] == "codex-cli" else ""
    if actual != expected:
        msg = (
            f"Codex CLI version mismatch: pinned={expected!r}, actual={actual!r} "
            f"(`{codex_path} --version` -> {output!r}). "
            "Update the repository pin and worker image together, or install the pinned version."
        )
        raise CodexCliVersionMismatch(msg)
    return actual


@functools.cache
def _ensure_codex_version_pinned() -> str:
    """Cache the runtime pin check to one shell-out per process."""
    skip = MetaprocEnv.METAPROC_SKIP_CODEX_VERSION_CHECK.read_str(default="").lower()
    if skip in ("1", "true", "yes"):
        return PINNED_CODEX_CLI_VERSION
    return verify_codex_version()


def _validate_codex_creds_blob(blob: str) -> None:
    """Raise if *blob* is not a ChatGPT-OAuth ``auth.json`` payload.

    API-key blobs (``tokens.auth_mode == "apikey"``) are explicitly
    rejected: API keys are stateless, don't need pooling, and should
    arrive as ``OPENAI_API_KEY`` via secret_variables rather than a
    pool label. This matches ``CodexCliAdapter.bootstrap``'s existing
    guard — the pool's push path shares the same invariant.
    """
    try:
        parsed: Any = json.loads(blob)
    except json.JSONDecodeError as exc:
        msg = f"Codex credentials blob is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"Codex credentials blob must be a JSON object, got {type(parsed).__name__}"
        raise RuntimeError(msg)
    payload = cast("dict[str, Any]", parsed)
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        msg = (
            "Codex credentials blob is missing required top-level key 'tokens'; "
            "refusing to write a malformed auth.json"
        )
        raise RuntimeError(msg)
    auth_mode = cast("dict[str, Any]", tokens).get("auth_mode")
    if auth_mode != "chatgpt":
        msg = (
            f"Codex credentials blob has tokens.auth_mode={auth_mode!r}; the pool "
            "only accepts ChatGPT-OAuth payloads. API-key auth is stateless and "
            "should arrive as OPENAI_API_KEY via secret_variables, not a pool label."
        )
        raise RuntimeError(msg)


def resolve_codex_home(env: dict[str, str] | None = None) -> Path:
    """Return the effective codex-cli home directory.

    Single source of truth for ``$CODEX_HOME`` resolution: read through the
    ``MetaprocEnv`` registry (so the env-var coverage gate protects future
    callers) and fall back to ``~/.codex``. Both the codex adapter
    (check_auth / bootstrap / prepare_env) and the ``metaproc codex-auth``
    CLI route through this helper so they cannot disagree about which
    directory holds the session.

    When *env* is provided, look it up there first (used by ``prepare_env``
    when the dispatch env hasn't been written to ``os.environ`` yet).
    """
    if env is not None:
        override = env.get("CODEX_HOME")
        if override:
            return Path(override)
    override = MetaprocEnv.CODEX_HOME.read_str(default=None)
    if override:
        return Path(override)
    return Path.home() / ".codex"


_CODEX_ALLOWED_KEYS = frozenset(
    {
        "append_system_prompt",
        "approval_policy",
        "config_overrides",
        "effort",
        "estimated_process_rss_bytes",
        "estimated_process_rss_mb",
        "host_max_concurrency",
        "initial_memory_budget_fraction",
        "model",
        "permission_mode",
        "sandbox",
        "timeout_s",
        "tools",
        "web_search",
        "working_directory",
    }
)

# permission_mode -> (sandbox, approval_policy). bypassPermissions is special —
# it emits --dangerously-bypass-approvals-and-sandbox instead of --sandbox/--ask-for-approval.
_PERMISSION_MODE_MAP: dict[str, tuple[str, str]] = {
    "default": ("workspace-write", "on-request"),
    "acceptEdits": ("workspace-write", "on-failure"),
    "plan": ("read-only", "untrusted"),
}

# Full set of accepted permission_mode values for validate_config — the
# 3 mapped modes plus the bypassPermissions special case handled inline.
_VALID_PERMISSION_MODES: frozenset[str] = frozenset(
    {*_PERMISSION_MODE_MAP.keys(), "bypassPermissions"}
)

# Canonical tool names that trigger --search (top-level flag). All other tools
# are satisfied implicitly by the sandbox policy and get log.debug-dropped,
# matching pi_cli.py:120's convention for unsupported tools.
_SEARCH_TOOLS = frozenset({"WebSearch", "WebFetch"})
_IMPLICIT_TOOLS = frozenset({"Bash", "Read", "Write", "Edit", "Grep", "Glob"})


def _build_codex_flag_groups(
    merged_config: dict[str, object],
    variables: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Build (top_level_flags, exec_flags, prompt_parts) from merged config.

    codex has two distinct flag parsers: several flags live **only** on the
    top-level ``codex`` parser (``-a/--ask-for-approval``, ``--search``) and
    others live **only** on ``codex exec`` (``--skip-git-repo-check``,
    ``--json``, ``--output-last-message``). The command must be built as
    ``codex <top-level> exec <exec> [PROMPT]``; running
    ``codex exec -a never …`` fails with ``unexpected argument``.

    prompt_parts holds any append_system_prompt content that will be inlined
    into the prompt text passed as the positional argv (Codex has no
    ``@<file>`` surrogate; this mirrors Gemini's read-and-inline pattern).
    """
    top_level: list[str] = []
    exec_flags: list[str] = []
    prompt_parts: list[str] = []

    model = merged_config.get("model") or CODEX_DEFAULT_MODEL
    model_str = str(model)
    if model_str in CODEX_VALID_MODELS:
        top_level.extend(["-m", model_str])
    else:
        log.warning(
            "codex-cli: ignoring unknown model name %r; falling back to default %r",
            model_str,
            CODEX_DEFAULT_MODEL,
        )
        top_level.extend(["-m", CODEX_DEFAULT_MODEL])

    effort = str(merged_config.get("effort") or CODEX_DEFAULT_EFFORT)
    top_level.extend(["-c", f"model_reasoning_effort={effort}"])

    # Sandbox + approval resolution. Explicit sandbox/approval_policy win over
    # permission_mode; permission_mode is the preferred integration point.
    permission_mode = merged_config.get("permission_mode")
    explicit_sandbox = merged_config.get("sandbox")
    explicit_approval = merged_config.get("approval_policy")

    if permission_mode == "bypassPermissions":
        top_level.append("--dangerously-bypass-approvals-and-sandbox")
    elif explicit_sandbox or explicit_approval:
        if explicit_sandbox:
            top_level.extend(["--sandbox", str(explicit_sandbox)])
        if explicit_approval:
            top_level.extend(["-a", str(explicit_approval)])
    elif permission_mode in _PERMISSION_MODE_MAP:
        sandbox_mode, approval = _PERMISSION_MODE_MAP[str(permission_mode)]
        top_level.extend(["--sandbox", sandbox_mode])
        top_level.extend(["-a", approval])
    else:
        msg = (
            "codex-cli: need one of permission_mode (bypassPermissions, default, "
            "acceptEdits, plan) or an explicit sandbox+approval_policy pair. "
            "Without one of these, codex prompts for permission on every tool call "
            "and deadlocks in batch mode (stdin is /dev/null)."
        )
        raise ValueError(msg)

    # Tools: WebSearch/WebFetch -> --search (top-level); implicit tools are
    # satisfied by the sandbox policy; everything else is log.debug-dropped.
    tools_value = merged_config.get("tools")
    web_search_flag = bool(merged_config.get("web_search"))
    if isinstance(tools_value, list):
        for tool in cast("list[Any]", tools_value):
            name = str(tool)
            if name in _SEARCH_TOOLS:
                web_search_flag = True
            elif name in _IMPLICIT_TOOLS:
                continue
            else:
                log.debug("codex-cli: dropping unsupported tool %r", name)
    if web_search_flag:
        top_level.append("--search")

    # Free-form TOML overrides. Accepted as a dict of dotted keys -> values.
    config_overrides = merged_config.get("config_overrides")
    if isinstance(config_overrides, dict):
        for key, value in cast("dict[str, Any]", config_overrides).items():
            # `codex -c key=value` — value is parsed as TOML by codex, with a
            # fallback to the raw string. JSON encoding here lets booleans /
            # numbers / quoted strings all round-trip cleanly.
            top_level.extend(["-c", f"{key}={json.dumps(value)}"])

    exec_flags.append("--skip-git-repo-check")

    working_dir = merged_config.get("working_directory")
    if working_dir:
        exec_flags.extend(["-C", str(working_dir)])

    append_system_prompt = merged_config.get("append_system_prompt")
    if append_system_prompt:
        resolved_sys = resolve_templates(str(append_system_prompt), variables).strip()
        if resolved_sys:
            prompt_parts.append(resolved_sys)

    return top_level, exec_flags, prompt_parts


class CodexCliAdapter:
    """Adapter for OpenAI Codex CLI (``codex``)."""

    adapter_type: str = "codex-cli"
    short_name: str = "codex-cli"
    default_model: str | None = CODEX_DEFAULT_MODEL

    # ── AuthCapableCliAdapter subprotocol (plan-2026-04-21 P1.2b) ──
    # The slot layout writes `<slot_dir>/.codex/auth.json` plus a
    # minimal `<slot_dir>/.codex/config.toml`. credential_scope_env
    # points CODEX_HOME at `<slot_dir>/.codex` — codex-cli then reads
    # auth.json from there instead of the OS keychain or ~/.codex.
    slot_credential_filename: str = ".codex/auth.json"
    # OQ9 (opt-in only): Claude ↔ Codex compatibility is semantic
    # (prompt/tool surface), not credential-blob, so it's declared
    # by operators in step spec rather than baked in here.
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        _ensure_codex_version_pinned()
        top_level, exec_flags, prompt_parts = _build_codex_flag_groups(merged_config, variables)

        # Codex has no @<file> surrogate and the engine launches subprocesses
        # with stdin=DEVNULL, so the prompt must be read from disk and passed
        # as a trailing positional argv. Mirrors gemini.py's approach. Fail
        # fast on a missing file rather than passing a literal "@/path"
        # string that codex would interpret as the prompt body.
        if not prompt_file.exists():
            msg = f"codex-cli: prompt file does not exist: {prompt_file}"
            raise FileNotFoundError(msg)
        prompt_body = prompt_file.read_text()
        prompt_text = "\n\n".join([*prompt_parts, prompt_body]) if prompt_parts else prompt_body

        return [
            "codex",
            *top_level,
            "exec",
            "--json",
            *exec_flags,
            prompt_text,
        ]

    def validate_config(self, merged_config: dict[str, object]) -> list[ConfigRejection]:
        rejections: list[ConfigRejection] = []
        for key, value in merged_config.items():
            if key not in _CODEX_ALLOWED_KEYS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"{key!r} is not supported by codex-cli")
                )
                continue
            if key == "model" and str(value) not in CODEX_VALID_MODELS:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=f"unknown codex-cli model {value!r}",
                    )
                )
            elif key == "effort" and str(value) not in CODEX_VALID_EFFORTS:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=(
                            f"unknown codex-cli effort {value!r} "
                            f"(expected one of {sorted(CODEX_VALID_EFFORTS)})"
                        ),
                    )
                )
            elif key == "sandbox" and str(value) not in CODEX_VALID_SANDBOX_MODES:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=(
                            f"unknown codex-cli sandbox mode {value!r} "
                            f"(expected one of {sorted(CODEX_VALID_SANDBOX_MODES)})"
                        ),
                    )
                )
            elif key == "approval_policy" and str(value) not in CODEX_VALID_APPROVAL_POLICIES:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=(
                            f"unknown codex-cli approval_policy {value!r} "
                            f"(expected one of {sorted(CODEX_VALID_APPROVAL_POLICIES)})"
                        ),
                    )
                )
            elif key == "permission_mode" and str(value) not in _VALID_PERMISSION_MODES:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=(
                            f"unknown codex-cli permission_mode {value!r} "
                            f"(expected one of {sorted(_VALID_PERMISSION_MODES)})"
                        ),
                    )
                )
        return rejections

    def prepare_env(
        self,
        env: dict[str, str],
        _merged_config: dict[str, object],
    ) -> dict[str, str]:
        out = dict(env)
        home = out.get("HOME")
        if home:
            # Pin CODEX_HOME so codex-cli uses the bootstrapped credentials
            # under home/.codex/ rather than whatever stray session lives on
            # the shared VM. Explicitly setting it also stops leaks when
            # multiple pool workers share a VM and rely on the task-scoped
            # HOME isolation instead of the default ~/.codex/ resolution.
            # If the dispatch env already carries an explicit CODEX_HOME
            # (operator-set), respect it via the shared resolver.
            codex_home = resolve_codex_home(out) if out.get("CODEX_HOME") else Path(home) / ".codex"
            out.setdefault("CODEX_HOME", str(codex_home))
            # codex-cli aborts with "CODEX_HOME points to '…', but that path
            # does not exist" when the directory is absent, even in API-key
            # mode. bootstrap() only materializes the dir when a creds blob
            # is present, so pre-create it here to keep API-key dispatch
            # working. Idempotent with bootstrap's own mkdir.
            try:
                codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError:
                log.warning(
                    "codex-cli: failed to pre-create CODEX_HOME=%s", codex_home, exc_info=True
                )
        return out

    def working_directory(self, merged_config: dict[str, object]) -> Path | None:
        value = merged_config.get("working_directory")
        return Path(str(value)) if value else None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        # codex-cli 0.124.0 uses flat top-level `type` keys (no msg.type
        # nesting). Both terminal events signal end-of-stream: turn.completed
        # (success, carries `usage` inline) and turn.failed (failure, carries
        # error.message). Intermediate `error` events are codex-internal
        # reconnects and are NOT terminal.
        return parse_jsonl_event(line, "turn.completed") or parse_jsonl_event(line, "turn.failed")

    def check_auth(self) -> AuthStatus:
        cli_path = shutil.which("codex")
        api_key = MetaprocEnv.OPENAI_API_KEY.read_str(default=None)
        # Honor $CODEX_HOME via the shared helper so this stays in sync with
        # codex_auth.py and codex-cli itself. Without this, an operator
        # using a non-default CODEX_HOME would see "no creds found" here
        # while `metaproc codex-auth show` reported a valid session.
        auth_json = resolve_codex_home() / "auth.json"

        if not cli_path:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=False,
                cli_path=None,
                credentials_found=False,
                auth_mode="none",
                details="codex CLI not found on PATH",
                setup_hint=CODEX_CLI_INSTALL_HINT,
            )

        try:
            verify_codex_version(codex_path=cli_path)
        except CodexCliVersionMismatch as exc:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=False,
                cli_path=cli_path,
                credentials_found=False,
                auth_mode="none",
                details=f"Codex CLI is present but unusable: {exc}",
                setup_hint=CODEX_CLI_INSTALL_HINT,
            )

        if api_key:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="api-key",
                details="OPENAI_API_KEY is set (Vehicle A)",
                setup_hint="",
            )

        if auth_json.is_file():
            auth_mode_str, details = _inspect_auth_json(auth_json)
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode=auth_mode_str,
                details=details,
                setup_hint="",
            )

        return AuthStatus(
            adapter_type=self.adapter_type,
            cli_found=True,
            cli_path=cli_path,
            credentials_found=False,
            auth_mode="none",
            details="No OPENAI_API_KEY and no ~/.codex/auth.json session found",
            setup_hint=(
                "Option 1: export OPENAI_API_KEY=sk-...\n"
                "Option 2: codex login   (ChatGPT OAuth, subscription billing)"
            ),
        )

    def auth_info(self) -> str:
        return (
            "Codex CLI auth modes:\n"
            "\n"
            "  1. API key (recommended for batch runs — metered per-token):\n"
            "     export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  2. ChatGPT OAuth (subscription-billed, free-per-request):\n"
            "     codex login\n"
        )

    def bootstrap(self, home: Path) -> None:
        # Materialize the ChatGPT-OAuth credential from the CODEX_CREDS_JSON
        # env var (set by the Batch Secret Manager binding) to
        # ~/.codex/auth.json, then unset the env var so the payload does not
        # leak to child process environments. No-op when the env var is
        # absent — laptop/local-dev paths are unaffected.
        # Also no-op under the per-slot pool path (METAPROC_AUTH_POOL_RUN=1),
        # which materializes into the slot dir, not the container home.
        if MetaprocEnv.METAPROC_AUTH_POOL_RUN.read_str(default="") == "1":
            return
        creds_json = os.environ.pop(CODEX_CREDS_ENV_VAR, "")
        if not creds_json:
            return

        _validate_codex_creds_blob(creds_json)

        # Honor $CODEX_HOME via the shared helper; fall back to <home>/.codex
        # when CODEX_HOME is unset. The bootstrap contract is per-task: the
        # engine passes a per-task `home` so multiple pool workers stay
        # isolated, but if the operator has explicitly set CODEX_HOME we
        # respect that override (matches codex-cli's own behavior).
        env_home = MetaprocEnv.CODEX_HOME.read_str(default=None)
        codex_dir = Path(env_home) if env_home else home / ".codex"
        codex_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        codex_dir.chmod(0o700)
        auth_file = codex_dir / "auth.json"
        auth_file.write_text(creds_json)
        auth_file.chmod(0o600)
        log.info("codex auth: materialized %s", auth_file)

    # ── AuthCapableCliAdapter methods ───────────────────────────────

    def debug_capture_args(self, _slot_dir: Path) -> list[str]:
        """codex-cli has no equivalent of ``claude -d api`` today.

        Returning ``[]`` opts out of the per-slot debug capture. If
        codex-cli grows a request-side debug flag, populate this with
        the analogous capture path (``<slot_dir>/codex-cli-debug.log``)
        and add the same name to :meth:`diagnostic_filenames` so the
        orchestrator preserves it next to the session log.
        """
        return []

    def diagnostic_filenames(self) -> tuple[str, ...]:
        # codex-cli writes no per-slot diagnostic logs today. Returns
        # () so preserve_diagnostics is a no-op for codex slots; when
        # codex grows a request-side debug flag (see
        # :meth:`debug_capture_args`), return the corresponding
        # ``codex-cli-debug.log`` here so it joins the same .logs/
        # tree the claude debug log already lives in.
        return ()

    def credential_scope_env(
        self,
        slot_dir: Path,
        *,
        vehicle: Any = None,
        blob: str = "",
    ) -> dict[str, str]:
        """Scope codex-cli credential lookup to ``<slot_dir>/.codex``.

        codex-cli reads ``<CODEX_HOME>/auth.json`` + ``config.toml`` at
        startup (spawn-boundary freeze — same pattern as Claude Code's
        CLAUDE_CONFIG_DIR). The pool slot writes both files under
        ``slot_dir/.codex/`` and points CODEX_HOME there.

        ``vehicle`` and ``blob`` are accepted for Protocol parity with
        Claude Code (Vehicle A token-only mode); codex is a single-vehicle
        adapter today and ignores them.
        """
        del vehicle, blob  # Protocol parity; unused for codex.
        return {"CODEX_HOME": str(slot_dir / ".codex")}

    def credential_scrub_env(self, *, vehicle: Any = None) -> dict[str, str]:
        """Unset higher-precedence codex auth vars for a pool slot.

        codex-cli's CODEX_CREDS_JSON env-bridge (Batch Secret Manager
        binding) would otherwise race the slot's auth.json during
        bootstrap. ``OPENAI_API_KEY`` is deliberately NOT scrubbed —
        enterprise API-key mode is an explicit operator choice; the
        slot coordinator refuses slot acquisition on that signal
        instead of silently clobbering it (parallel to the
        ANTHROPIC_API_KEY rule for Claude).

        ``vehicle`` is accepted for Protocol parity; codex is a
        single-vehicle adapter today.
        """
        del vehicle  # Protocol parity; unused for codex.
        return {
            CODEX_CREDS_ENV_VAR: "",
        }

    def materialize_credential(
        self,
        slot_dir: Path,
        blob: str,
        *,
        vehicle: Any = None,
    ) -> None:
        """Write *blob* to ``<slot_dir>/.codex/auth.json`` 0600 + config.toml.

        The accompanying minimal ``config.toml`` pins
        ``cli_auth_credentials_store="file"`` and
        ``forced_login_method="chatgpt"`` so the slot's credential
        lookup never leaks out to the OS keychain or a stray
        OPENAI_API_KEY path. Creates ``slot_dir/.codex/`` mode 0700
        if missing. Never touches ``~/.codex/``.

        ``vehicle`` is accepted for Protocol parity with Claude Code's
        Vehicle A token-only mode; codex is a single-vehicle adapter
        today and uses the file-materialization path unconditionally.
        """
        del vehicle  # Protocol parity; unused for codex.
        _validate_codex_creds_blob(blob)
        codex_dir = slot_dir / ".codex"
        codex_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        codex_dir.chmod(0o700)
        auth_file = codex_dir / "auth.json"
        auth_file.write_text(blob)
        auth_file.chmod(0o600)
        config_file = codex_dir / "config.toml"
        config_file.write_text(_CODEX_SLOT_CONFIG_TOML)
        config_file.chmod(0o600)

    def capture_credential(self) -> str:
        """Read the operator's live ``~/.codex/auth.json`` for a pool push.

        Used only by ``metaproc auth push``. Unlike Claude (which
        requires reading the macOS Keychain), codex-cli writes
        auth.json directly to ``$CODEX_HOME`` or ``~/.codex/``, so
        this is a plain filesystem read on any platform the operator
        has run ``codex login`` on. Raises ``RuntimeError`` with an
        actionable message if the file is absent — the operator
        retries after ``codex login``.
        """
        codex_dir = resolve_codex_home()
        auth_file = codex_dir / "auth.json"
        if not auth_file.exists():
            msg = (
                f"Codex credential capture requires an auth.json at {auth_file}. "
                "Run `codex login --with-chatgpt-oauth` on this machine first."
            )
            raise RuntimeError(msg)
        try:
            blob = auth_file.read_text()
        except OSError as exc:
            msg = f"Failed to read Codex credentials at {auth_file}: {exc}"
            raise RuntimeError(msg) from exc
        _validate_codex_creds_blob(blob)
        return blob

    def classify_failure(
        self,
        exc: BaseException | None,
        stderr: str,
        session_log_path: Path | None,
    ) -> AuthFailureClassification:
        """Classify a Codex CLI failure.

        Phase 1 stub: the codex-cli error surface is narrower than
        Claude's (no structured rate_limit_event in the session log
        today, per Codex-auth research §F6). We cover the obviously
        terminal refresh-on-401 signature and a generic too-many-
        requests match. Everything else is ``unknown`` so the generic
        retry classifier wins. First-class treatment — quota
        visibility, cooling_until_ts extraction, cross-provider
        fallback — lands in P3.2 after the Codex-adapter research
        brief settles on an observable signal.

        Plan §Phase 5: each return path populates ``severity`` on the
        three-way taxonomy and runs the known-bug detector first so
        software bugs abort cleanly rather than burning retries.
        """

        del exc  # reserved for future structured hand-off

        stderr_lc = stderr.lower()

        # 1. Terminal auth signals → expired (ABORT). Must run before
        # the known-bug detector so a broad silent-exit-1 signature
        # can't mask a dead Codex refresh token and leave a bad
        # credential live in the pool.
        if "invalid_grant" in stderr_lc or "unauthorized" in stderr_lc:
            return AuthFailureClassification(
                status="expired",
                reason="codex-unauthorized/invalid_grant",
                severity=FailureSeverity.ABORT,
            )

        # 2. Known-bug signatures short-circuit the rest.
        session_log_text = ""
        if session_log_path is not None and session_log_path.exists():
            try:
                session_log_text = session_log_path.read_text()
            except OSError:
                session_log_text = ""
        bug = detect_known_bug(stderr=stderr, session_log_text=session_log_text)
        if bug is not None:
            return AuthFailureClassification(
                status="unknown",
                reason=f"known-bug:{bug.name}",
                severity=FailureSeverity.ABORT,
                known_bug_signature=bug.name,
            )

        # 3. Soft rate-limit family → cooling / RETRY_AFTER_WAIT.
        if "too_many_requests" in stderr_lc or "rate limit" in stderr_lc:
            return AuthFailureClassification(
                status="cooling",
                cooling_until_ts=None,
                reason="codex-rate-limit",
                severity=FailureSeverity.RETRY_AFTER_WAIT,
            )
        # 4. Transient signatures → RETRY_NOW severity so dispatch can
        # retry immediately; pool state unchanged.
        if any(
            token in stderr_lc for token in ("503", "502", "econnrefused", "econnreset", "timeout")
        ):
            return AuthFailureClassification(
                status="unknown",
                reason="transient-network-or-5xx",
                severity=FailureSeverity.RETRY_NOW,
            )
        return AuthFailureClassification(status="unknown", reason="")

    def flush_refreshed_credential(self, slot_dir: Path) -> str | None:
        """Return the current slot blob; caller fingerprints vs bootstrap."""
        auth_file = slot_dir / self.slot_credential_filename
        if not auth_file.exists():
            return None
        try:
            return auth_file.read_text()
        except OSError:
            return None

    def query_quota_usage(self, _slot_dir: Path) -> QuotaUsage | None:
        """Return ``None`` pending Codex native-quota signal (P3.2)."""
        # Codex-auth research §F6 notes refresh-on-401 as the
        # observable signal; no documented quota-headers endpoint
        # today. P3.2 adds either a refresh-attempt probe or a ping
        # endpoint once one exists.
        return None

    def query_live_quota(
        self,
        _slot_dir: Path,
        *,
        vehicle: Any = None,  # noqa: ARG002
        blob: str = "",  # noqa: ARG002
    ) -> QuotaUsage | None:
        """Return ``None`` pending Codex live-quota implementation
        (internal issue / Phase 1 of plan-2026-05-12-live-auth-quota-probes.md).

        The live endpoint is JSON-RPC ``account/rateLimits/read`` over stdio
        against a subprocess of ``codex app-server`` (Codex CLI ≥ 0.32; the
        local 0.31.0 does not ship ``app-server``). Stub implementation here
        so ``CodexCliAdapter`` still satisfies the ``AuthCapableCliAdapter``
        runtime-checkable Protocol after the protocol method was added.
        """
        return None

    def setup_token_command(self) -> list[str] | None:
        # Codex's ChatGPT-OAuth flow has no setup-token equivalent
        # today; `metaproc auth setup` is claude-only until a future
        # phase wires a codex token-mint surface.
        return None


def _inspect_auth_json(path: Path) -> tuple[str, str]:
    """Classify an auth.json file for check_auth reporting."""
    try:
        doc: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ("unknown", f"{path} exists but could not be parsed")
    if not isinstance(doc, dict):
        return ("unknown", f"{path} exists but is not a JSON object")
    tokens = cast("dict[str, Any]", doc).get("tokens")
    mode = tokens.get("auth_mode") if isinstance(tokens, dict) else None
    if mode == "chatgpt":
        return ("interactive-login", f"Using ChatGPT OAuth session ({path})")
    if mode == "apikey":
        return ("api-key", f"Using API-key session ({path})")
    return ("unknown", f"{path} exists but has no tokens.auth_mode")
