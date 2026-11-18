"""Codex CLI adapter (OpenAI Codex CLI, ``codex exec --json``)."""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from strif import atomic_output_file

from metaproc.adapters.base import (
    AuthFailureClassification,
    AuthStatus,
    ConfigRejection,
    FailureSeverity,
    NativeSessionLogSet,
    QuotaUsage,
    parse_jsonl_event,
    resolve_templates,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.config.model_catalog import resolve_model
from metaproc.dispatch.known_bugs import detect_known_bug
from metaproc.io import write_secret_text
from metaproc.settings import (
    CODEX_DEFAULT_EFFORT,
    CODEX_DEFAULT_MODEL,
    CODEX_VALID_APPROVAL_POLICIES,
    CODEX_VALID_EFFORTS,
    CODEX_VALID_MODELS,
    CODEX_VALID_SANDBOX_MODES,
)

# Env var carrying the ChatGPT-OAuth ~/.codex/auth.json payload to Batch workers.
# See src/metaproc/docs/metaproc-design.md.
CODEX_CREDS_ENV_VAR = "CODEX_CREDS_JSON"

# Minimal config.toml emitted alongside auth.json in a pool slot. The two
# knobs tell codex-cli to read credentials from the slot-local auth.json
# instead of the OS keychain and to pin the ChatGPT OAuth path rather
# than letting an ambient OPENAI_API_KEY silently take over. Kept small
# on purpose so future fields don't regress the pool's isolation guarantees.
_CODEX_SLOT_CONFIG_TOML = 'cli_auth_credentials_store = "file"\nforced_login_method = "chatgpt"\n'

log = logging.getLogger(__name__)

PINNED_CODEX_CLI_VERSION = "0.166.0"
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


@functools.cache
def _codex_version_drift() -> str | None:
    """Return a drift message if the on-PATH ``codex`` mismatches the pin, else
    None. Non-blocking: drift is surfaced as a prominent warning, not a hard
    error. Honors ``METAPROC_SKIP_CODEX_VERSION_CHECK``.
    """
    skip = MetaprocEnv.METAPROC_SKIP_CODEX_VERSION_CHECK.read_str(default="").lower()
    if skip in ("1", "true", "yes"):
        return None
    try:
        verify_codex_version()
    except CodexCliVersionMismatch as exc:
        log.warning("harness CLI version drift — %s", exc)
        return str(exc)
    return None


def codex_auth_json_mode(payload: dict[str, Any]) -> str | None:
    """Return the login mode Codex resolves for a parsed ``auth.json`` object.

    Codex writes ``auth_mode`` at the top level of ``auth.json``
    (``codex-rs/login/src/auth/storage.rs`` ``AuthDotJson``, 0.147.0 and
    0.166.0); its ``tokens`` block has no such field. Earlier Metaproc releases
    read and documented ``tokens.auth_mode`` instead, so that nested spelling is
    still honored when the top-level field is absent. A file with neither follows
    Codex's own ``AuthDotJson::resolved_mode`` fallback: a stored API key means
    ``apikey``, ``tokens`` alone means ``chatgpt``. Returns ``None`` when the
    payload carries no credential Metaproc can classify.
    """
    top_level = payload.get("auth_mode")
    if isinstance(top_level, str) and top_level:
        return top_level
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        nested = cast("dict[str, Any]", tokens).get("auth_mode")
        if isinstance(nested, str) and nested:
            return nested
    if payload.get("personal_access_token"):
        return "personalAccessToken"
    if payload.get("bedrock_api_key"):
        return "bedrockApiKey"
    if payload.get("bedrock_access_keys"):
        return "bedrockAccessKeys"
    if payload.get("OPENAI_API_KEY"):
        return "apikey"
    if isinstance(tokens, dict):
        return "chatgpt"
    return None


def _validate_codex_creds_blob(blob: str) -> None:
    """Raise if *blob* is not a ChatGPT-OAuth ``auth.json`` payload.

    The login mode is read as Codex reads it (:func:`codex_auth_json_mode`).
    API-key blobs (``auth_mode == "apikey"``) are explicitly
    rejected: API keys are stateless, don't need pooling, and should
    arrive as ``OPENAI_API_KEY`` through container-side secret hydration rather than a
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
    auth_mode = codex_auth_json_mode(payload)
    if auth_mode != "chatgpt":
        msg = (
            f"Codex credentials blob has auth_mode={auth_mode!r}; the pool "
            "only accepts ChatGPT-OAuth payloads. API-key auth is stateless and "
            "should arrive as OPENAI_API_KEY through cloud secret hydration, "
            "not a pool label."
        )
        raise RuntimeError(msg)


def resolve_codex_home(env: Mapping[str, str] | None = None) -> Path:
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


# The `.env.example` placeholder for CODEX_API_KEY. A copied template that still
# carries it must not outrank a real login: Codex gives the env key precedence.
_CODEX_API_KEY_PLACEHOLDER = f"REPLACE_WITH_{MetaprocEnv.CODEX_API_KEY.name}"

# A personal access token or agent-identity JWT. Codex 0.166.0 uses it ahead of
# any stored login, and only CODEX_API_KEY outranks it (`load_auth` in
# codex-rs/login/src/auth/manager.rs).
_CODEX_ACCESS_TOKEN_ENV = "CODEX_ACCESS_TOKEN"

_CREDENTIAL_STORE_KEY = "cli_auth_credentials_store"

# Where Codex reads its system config, requirements and legacy managed config on Unix.
_CODEX_UNIX_SYSTEM_DIR = Path("/etc/codex")

# macOS configuration profiles deliver forced preferences for this domain, and
# Codex reads config and requirements TOML from them (codex-rs/config/src/loader/macos.rs).
_MACOS_MANAGED_PREFERENCES_DIR = Path("/Library/Managed Preferences")
_MACOS_CODEX_PREFERENCES_PLIST = "com.openai.codex.plist"

# The variables whose values decide the bridge (see `_CodexChildEnv`).
_BRIDGE_INPUT_VARS = frozenset(
    {
        "CODEX_HOME",
        _CODEX_ACCESS_TOKEN_ENV,
        MetaprocEnv.CODEX_API_KEY.name,
        MetaprocEnv.OPENAI_API_KEY.name,
    }
)


def _codex_env_value(env: Mapping[str, str], name: str) -> str:
    """Return *name* from *env* the way Codex reads it: trimmed, so blank means unset."""
    return env.get(name, "").strip()


def _codex_system_dir() -> Path:
    if sys.platform == "win32":
        program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
        return Path(program_data) / "OpenAI" / "Codex"
    return _CODEX_UNIX_SYSTEM_DIR


def _credential_store_files(codex_home: Path, cwd: Path) -> list[Path]:
    """Return the TOML files that can set Codex's `cli_auth_credentials_store`.

    Codex 0.166.0 layers, besides `$CODEX_HOME/config.toml`, the system
    `requirements.toml` and `config.toml`, the legacy `managed_config.toml`
    (Unix), and `.codex/config.toml` in the working directory and its parents up
    to the project root (`load_config_layers_state` in
    codex-rs/config/src/loader/mod.rs). Project files apply only to a trusted
    project, and the scan stops at the project root. Both are Codex's decisions,
    so every parent is listed here. A `--profile` file is not, because Metaproc
    passes no `--profile`; nor is the enterprise cloud layer, which Codex fetches
    only with a ChatGPT login (`codex exec` bootstrap), and the bridge has already
    ruled one out.
    """
    system_dir = _codex_system_dir()
    files = [
        system_dir / "requirements.toml",
        system_dir / "config.toml",
        codex_home / "config.toml",
    ]
    if sys.platform != "win32":
        files.append(system_dir / "managed_config.toml")
    resolved_home = codex_home.resolve()
    for directory in (cwd, *cwd.parents):
        dot_codex = directory / ".codex"
        # Codex skips a project `.codex` that is CODEX_HOME itself.
        if dot_codex.resolve() != resolved_home:
            files.append(dot_codex / "config.toml")
    return files


def _file_store_blocker(path: Path) -> str | None:
    """Return why *path* may select a store other than `file`, or None when it does not."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError, NotADirectoryError:
        return None
    except OSError, UnicodeDecodeError:
        return f"{path} could not be read"
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return f"{path} could not be parsed"
    value = doc.get(_CREDENTIAL_STORE_KEY, "file")
    return None if value == "file" else f"{path} sets {_CREDENTIAL_STORE_KEY}={value!r}"


def _credential_store_blocker(
    codex_home: Path, merged_config: Mapping[str, object], cwd: Path | None = None
) -> str | None:
    """Return why a Codex login may live outside auth.json, or None when none can.

    A login lives outside `$CODEX_HOME/auth.json` when `cli_auth_credentials_store`
    is `keyring` or `auto`. This fails safe. It returns a reason when any layer
    Codex may read names a store other than `file`, whatever that layer's
    precedence, and when a layer cannot be read. *cwd* is the directory Codex runs
    in: the step's `working_directory`, else this process's.
    """
    overrides = merged_config.get("config_overrides")
    if isinstance(overrides, dict):
        value = cast("dict[str, Any]", overrides).get(_CREDENTIAL_STORE_KEY)
        if value is not None and value != "file":
            return f"config_overrides set {_CREDENTIAL_STORE_KEY}={value!r}"
    if cwd is None:
        working_directory = merged_config.get("working_directory")
        cwd = Path(str(working_directory)) if working_directory else Path.cwd()
    for path in _credential_store_files(codex_home, cwd.absolute()):
        reason = _file_store_blocker(path)
        if reason is not None:
            return reason
    if sys.platform == "darwin" and _MACOS_MANAGED_PREFERENCES_DIR.is_dir():
        plists = [
            _MACOS_MANAGED_PREFERENCES_DIR / _MACOS_CODEX_PREFERENCES_PLIST,
            *_MACOS_MANAGED_PREFERENCES_DIR.glob(f"*/{_MACOS_CODEX_PREFERENCES_PLIST}"),
        ]
        for plist in plists:
            if plist.exists():
                return f"macOS managed preferences {plist} may set {_CREDENTIAL_STORE_KEY}"
    return None


@functools.cache
def _log_bridge(codex_home: str) -> None:
    """Log once per Codex home: the bridged key moves the run to API-key billing."""
    log.info(
        "codex-cli: passing OPENAI_API_KEY to Codex as %s; no stored Codex login in %s, "
        "so the run is billed to the API key",
        MetaprocEnv.CODEX_API_KEY.name,
        codex_home,
    )


def _bridged_codex_api_key(
    env: Mapping[str, str], merged_config: Mapping[str, object]
) -> str | None:
    """Return the ``OPENAI_API_KEY`` value to hand ``codex exec`` as ``CODEX_API_KEY``, or None.

    ``codex exec`` (0.147.0 and 0.166.0) reads only ``CODEX_API_KEY`` from the
    environment; it ignores ``OPENAI_API_KEY``, which Metaproc documents as the
    Codex API-key vehicle. Codex gives that env key precedence over every other
    credential, so it is passed only when nothing else can log in: no
    ``CODEX_ACCESS_TOKEN``, no ``auth.json`` in the effective ``CODEX_HOME``, and a
    file credential store in every config layer Codex reads. When the adapter
    cannot tell, it does not bridge and logs why at INFO. Only a run that would
    otherwise fail with 401 gains the key. Values are trimmed as Codex trims them,
    so a blank ``CODEX_API_KEY`` counts as unset, as does the ``.env.example``
    placeholder.
    """
    codex_key = _codex_env_value(env, MetaprocEnv.CODEX_API_KEY.name)
    if codex_key and codex_key != _CODEX_API_KEY_PLACEHOLDER:
        return None
    api_key = _codex_env_value(env, MetaprocEnv.OPENAI_API_KEY.name)
    if not api_key:
        return None
    if _codex_env_value(env, _CODEX_ACCESS_TOKEN_ENV):
        log.info(
            "codex-cli: not passing OPENAI_API_KEY to Codex; %s is set, and Codex would "
            "let the env key replace it",
            _CODEX_ACCESS_TOKEN_ENV,
        )
        return None
    codex_home = resolve_codex_home(env)
    if (codex_home / "auth.json").is_file():
        return None
    blocker = _credential_store_blocker(codex_home, merged_config)
    if blocker is not None:
        log.info(
            "codex-cli: not passing OPENAI_API_KEY to Codex; a login may be stored outside %s: %s",
            codex_home / "auth.json",
            blocker,
        )
        return None
    _log_bridge(str(codex_home))
    return api_key


class _CodexChildEnv(dict[str, str]):
    """The child environment ``prepare_env`` returns, with the ``CODEX_API_KEY`` bridge applied.

    Every launcher applies a step's authored ``env``, and any plugin env
    transform, with ``update()`` after ``prepare_env`` returns. So the bridge would
    otherwise decide against ``$HOME/.codex`` while Codex runs against a
    ``CODEX_HOME`` the step names, and a ChatGPT login there would lose to the
    env key. An ``update()`` or item assignment that touches a bridge input
    therefore withdraws the key the bridge set and decides again against the
    merged environment. A ``CODEX_API_KEY`` the step names, blank included, is
    the step's choice and is left as written. The object pickles and copies as a
    plain dict.
    """

    def __init__(self, env: Mapping[str, str], merged_config: Mapping[str, object]) -> None:
        super().__init__(env)
        self._merged_config = merged_config
        self._bridged: str | None = None
        self._step_named_codex_key = False
        self._decide()

    def __reduce__(self) -> tuple[type[dict[str, str]], tuple[dict[str, str]]]:
        return (dict, (dict(self),))

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._changed({key})

    def update(self, *args: Any, **kwargs: str) -> None:
        incoming: dict[str, str] = dict(*args, **kwargs)
        super().update(incoming)
        self._changed(set(incoming))

    def _changed(self, keys: set[str]) -> None:
        touched = keys & _BRIDGE_INPUT_VARS
        if not touched:
            return
        if MetaprocEnv.CODEX_API_KEY.name in touched:
            self._step_named_codex_key = True
        before = self._bridged
        self._decide()
        if before != self._bridged:
            log.info(
                "codex-cli: the step env sets %s; %s the OPENAI_API_KEY bridge to Codex",
                ", ".join(sorted(touched)),
                "applied" if self._bridged is not None else "withdrew",
            )

    def _decide(self) -> None:
        name = MetaprocEnv.CODEX_API_KEY.name
        current = super().get(name)
        if (
            not self._step_named_codex_key
            and self._bridged is not None
            and current == self._bridged
        ):
            super().__delitem__(name)
            current = None
        if current == _CODEX_API_KEY_PLACEHOLDER:
            log.warning("codex-cli: ignoring the %s placeholder copied from .env.example", name)
            super().__delitem__(name)
        self._bridged = None
        if self._step_named_codex_key:
            return
        bridged = _bridged_codex_api_key(self, self._merged_config)
        if bridged is not None:
            super().__setitem__(name, bridged)
            self._bridged = bridged


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
# The sandbox carries each mode's policy: `codex exec` forces approval to `never` in
# headless mode whatever `-a` says, so `-a` is only parse-validated. Codex removed
# `on-failure` from the flag in 0.143.0 and `untrusted` in 0.149.0, leaving
# `on-request` and `never`.
_PERMISSION_MODE_MAP: dict[str, tuple[str, str]] = {
    "default": ("workspace-write", "on-request"),
    "acceptEdits": ("workspace-write", "on-request"),
    "plan": ("read-only", "on-request"),
}

# Explicit `approval_policy` values the pinned CLI no longer parses. They are
# translated, with a warning, so existing process files keep running; the
# translation has no runtime effect because `codex exec` forces `never`.
CODEX_LEGACY_APPROVAL_POLICIES: dict[str, str] = {
    "on-failure": "on-request",
    "untrusted": "on-request",
}


@functools.cache
def _warn_legacy_approval_policy(requested: str, replacement: str) -> None:
    """Warn once per process per legacy value; fan-out builds many commands."""
    log.warning(
        "codex-cli approval_policy %r is no longer accepted by Codex %s; sending %r. "
        "`codex exec` forces approval to `never`, so the run's behavior is unchanged.",
        requested,
        PINNED_CODEX_CLI_VERSION,
        replacement,
    )


def _codex_approval_policy(value: object) -> str:
    """Return the `-a` value the pinned CLI accepts for a configured policy."""
    requested = str(value)
    replacement = CODEX_LEGACY_APPROVAL_POLICIES.get(requested)
    if replacement is None:
        return requested
    _warn_legacy_approval_policy(requested, replacement)
    return replacement


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

    ``prompt_parts`` holds any ``append_system_prompt`` content that is prepended
    to the file-backed stdin stream.
    """
    top_level: list[str] = []
    exec_flags: list[str] = []
    prompt_parts: list[str] = []

    model = resolve_model("codex-cli", merged_config.get("model"))
    top_level.extend(["-m", model])

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
            top_level.extend(["-a", _codex_approval_policy(explicit_approval)])
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

    def preflight(self) -> str | None:
        # Plan-time prerequisite check: surface any `codex` version drift at
        # launch (as a prominent warning, not a block) rather than mid-DAG.
        return _codex_version_drift()

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        _codex_version_drift()
        top_level, exec_flags, prompt_parts = _build_codex_flag_groups(merged_config, variables)

        # Codex reads "-" from stdin. Launch through a constant POSIX-shell
        # wrapper that redirects the prompt file, keeping large or sensitive
        # prompt bodies out of argv and avoiding ARG_MAX failures.
        if not prompt_file.exists():
            msg = f"codex-cli: prompt file does not exist: {prompt_file}"
            raise FileNotFoundError(msg)
        effective_prompt_file = prompt_file
        if prompt_parts:
            prompt_body = prompt_file.read_text(encoding="utf-8")
            effective_prompt_file = prompt_file.with_suffix(".codex-prompt.txt")
            with atomic_output_file(effective_prompt_file) as tmp_path:
                Path(tmp_path).write_text(
                    "\n\n".join([*prompt_parts, prompt_body]),
                    encoding="utf-8",
                )

        codex_command = [
            "codex",
            *top_level,
            "exec",
            "--json",
            *exec_flags,
            "-",
        ]
        return [
            "/bin/sh",
            "-c",
            'prompt_file=$1; shift; exec "$@" < "$prompt_file"',
            "metaproc-codex",
            str(effective_prompt_file),
            *codex_command,
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
        merged_config: dict[str, object],
    ) -> dict[str, str]:
        out = dict(env)
        self._pin_codex_home(out)
        return _CodexChildEnv(out, merged_config)

    @staticmethod
    def _pin_codex_home(out: dict[str, str]) -> None:
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

    def working_directory(self, merged_config: dict[str, object]) -> Path | None:
        value = merged_config.get("working_directory")
        return Path(str(value)) if value else None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        # `codex exec --json` (0.124.0 through the pinned 0.166.0) uses flat
        # top-level `type` keys (no msg.type nesting). Both terminal events
        # signal end-of-stream: turn.completed (success, carries `usage`
        # inline) and turn.failed (failure, carries error.message). Intermediate `error` events are codex-internal
        # reconnects and are NOT terminal.
        return parse_jsonl_event(line, "turn.completed") or parse_jsonl_event(line, "turn.failed")

    def check_auth(self) -> AuthStatus:
        cli_path = shutil.which("codex")
        # Trimmed as Codex trims them: a blank value counts as unset.
        api_key = (MetaprocEnv.OPENAI_API_KEY.read_str(default=None) or "").strip()
        codex_api_key = (MetaprocEnv.CODEX_API_KEY.read_str(default=None) or "").strip()
        access_token = _codex_env_value(os.environ, _CODEX_ACCESS_TOKEN_ENV)
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

        # Report the credential Codex will actually use, in Codex's order: an
        # explicit CODEX_API_KEY, then CODEX_ACCESS_TOKEN, then the stored
        # auth.json, then OPENAI_API_KEY, which reaches Codex only through
        # prepare_env's CODEX_API_KEY bridge.
        if codex_api_key and codex_api_key != _CODEX_API_KEY_PLACEHOLDER:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="api-key",
                details="CODEX_API_KEY is set; Codex prefers it over any stored login",
                setup_hint="",
            )

        if access_token:
            details = f"{_CODEX_ACCESS_TOKEN_ENV} is set; Codex uses it ahead of any stored login"
            if api_key:
                details += ", and runs do not pass OPENAI_API_KEY to Codex"
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="access-token",
                details=details,
                setup_hint="",
            )

        if auth_json.is_file():
            auth_mode_str, details = _inspect_auth_json(auth_json)
            if api_key:
                details += "; OPENAI_API_KEY is set but Codex uses this stored login"
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode=auth_mode_str,
                details=details,
                setup_hint="",
            )

        if api_key:
            blocker = _credential_store_blocker(auth_json.parent, {})
            if blocker is not None:
                return AuthStatus(
                    adapter_type=self.adapter_type,
                    cli_found=True,
                    cli_path=cli_path,
                    credentials_found=True,
                    auth_mode="unknown",
                    details=(
                        f"OPENAI_API_KEY is set, but a Codex login may be stored outside "
                        f"auth.json ({blocker}), so runs do not pass the key to Codex"
                    ),
                    setup_hint="",
                )
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="api-key",
                details=(
                    "OPENAI_API_KEY is set (Vehicle A); runs pass it to Codex as CODEX_API_KEY"
                ),
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
        write_secret_text(auth_file, creds_json)
        log.info("codex auth: materialized %s", auth_file)

    # ── AuthCapableCliAdapter methods ───────────────────────────────

    def debug_capture_args(self, _slot_dir: Path) -> list[str]:
        """codex-cli has no equivalent of Claude Code's ``--debug-file`` today.

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

    def native_session_log_sets(self) -> tuple[NativeSessionLogSet, ...]:
        # `codex exec` runs without `--ephemeral`, so it records each session as a
        # rollout under `$CODEX_HOME/sessions/YYYY/MM/DD/`. A pool slot sets
        # CODEX_HOME to `<slot_dir>/.codex`; auth.json and config.toml sit beside
        # `sessions/`, never inside it.
        return (
            NativeSessionLogSet(
                name="codex-sessions",
                root=".codex/sessions",
                filename_patterns=("rollout-*.jsonl", "rollout-*.jsonl.zst"),
            ),
        )

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
        # CODEX_API_KEY is what prepare_env bridges OPENAI_API_KEY into; the
        # slot's `forced_login_method = "chatgpt"` already makes Codex skip it,
        # and dropping it keeps an API key out of an OAuth slot's child env.
        return {
            CODEX_CREDS_ENV_VAR: "",
            MetaprocEnv.CODEX_API_KEY.name: "",
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
        write_secret_text(auth_file, blob)
        config_file = codex_dir / "config.toml"
        write_secret_text(config_file, _CODEX_SLOT_CONFIG_TOML)

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
                "Run `codex login` (or `codex login --device-auth`) on this machine first."
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
        (Phase 1 of plan-2026-05-12-live-auth-quota-probes.md).

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
        doc: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return ("unknown", f"{path} exists but could not be parsed")
    if not isinstance(doc, dict):
        return ("unknown", f"{path} exists but is not a JSON object")
    mode = codex_auth_json_mode(cast("dict[str, Any]", doc))
    if mode == "chatgpt":
        return ("interactive-login", f"Using ChatGPT OAuth session ({path})")
    if mode == "apikey":
        return ("api-key", f"Using API-key session ({path})")
    if mode is None:
        return ("unknown", f"{path} exists but holds no recognized Codex credential")
    return ("unknown", f"{path} uses Codex auth_mode={mode!r}, which Metaproc does not classify")


def codex_thread_id(stdout: str) -> str | None:
    """Return the thread ID from a ``codex exec --json`` stream's ``thread.started`` event."""
    for line in stdout.splitlines():
        event = parse_jsonl_event(line, "thread.started")
        if event is not None:
            thread_id = event.get("thread_id")
            return thread_id if isinstance(thread_id, str) and thread_id else None
    return None


def _read_rollout_text(rollout: Path) -> str | None:
    """Return a rollout's text, decompressing a ``.jsonl.zst`` one; None when unreadable."""
    if rollout.suffix != ".zst":
        return rollout.read_text(encoding="utf-8")
    try:
        from compression import zstd  # noqa: PLC0415 -- CPython may be built without libzstd
    except ImportError:
        log.debug("codex-cli: cannot read %s; this Python has no compression.zstd", rollout)
        return None
    try:
        with zstd.open(rollout, "rt", encoding="utf-8") as stream:
            return stream.read()
    except zstd.ZstdError, EOFError:
        return None


def codex_rollout_model(codex_home: Path, thread_id: str) -> str | None:
    """Return the model Codex recorded for a thread in its session rollout.

    ``codex exec`` writes ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread>.jsonl``,
    which Codex may later store compressed as ``.jsonl.zst``; both are read. Its
    ``turn_context`` record carries the model Codex sent the request for after
    its own config and profile resolution. That is the *requested* model:
    ``codex exec`` (0.166.0) reports no model the provider served, in the event
    stream or the rollout. Returns None when no readable rollout for the thread
    exists or it names no model.
    """
    sessions = codex_home / "sessions"
    # The thread ID becomes a glob pattern; accept only the UUID alphabet Codex uses.
    if not thread_id or not all(ch.isalnum() or ch == "-" for ch in thread_id):
        return None
    if not sessions.is_dir():
        return None
    rollouts = sorted(
        path
        for pattern in (f"rollout-*-{thread_id}.jsonl", f"rollout-*-{thread_id}.jsonl.zst")
        for path in sessions.rglob(pattern)
    )
    for rollout in rollouts:
        try:
            text = _read_rollout_text(rollout)
        except OSError, UnicodeDecodeError:
            continue
        for line in (text or "").splitlines():
            record = parse_jsonl_event(line, "turn_context")
            if record is None:
                continue
            payload = record.get("payload")
            model = (
                cast("dict[str, Any]", payload).get("model") if isinstance(payload, dict) else None
            )
            if isinstance(model, str) and model:
                return model
    return None
