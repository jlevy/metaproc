"""Gemini CLI adapter."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
from hashlib import sha256
from pathlib import Path
from typing import cast

from metaproc.adapters.base import AuthStatus, ConfigRejection, parse_jsonl_event
from metaproc.adapters.cli_version import (
    CliVersionMismatch,
    CliVersionSpec,
    check_cli_version,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.config.model_catalog import resolve_model
from metaproc.io import temp_output_dir, write_secret_text
from metaproc.io.mkdir_lock import mkdir_lock
from metaproc.settings import (
    GEMINI_DEFAULT_MODEL,
    GEMINI_DEFAULT_NATIVE_SETTINGS,
    GEMINI_DYNAMIC_MODEL_SETTINGS,
    GEMINI_SESSION_RETENTION_SETTINGS,
    GEMINI_VALID_MODELS,
)

log = logging.getLogger(__name__)

PINNED_GEMINI_CLI_VERSION = "0.59.0"
# The auth hint and the version refusal quote one command, so a pin bump moves both.
GEMINI_CLI_INSTALL_COMMAND = f"npm install -g @google/gemini-cli@{PINNED_GEMINI_CLI_VERSION}"
GEMINI_CLI_INSTALL_HINT = f"Install: {GEMINI_CLI_INSTALL_COMMAND}"


class GeminiCliVersionMismatch(CliVersionMismatch):
    """Raised when the on-PATH ``gemini`` binary does not match the pin."""


_GEMINI_VERSION_SPEC = CliVersionSpec(
    label="Gemini",
    cli_path="gemini",
    expected=PINNED_GEMINI_CLI_VERSION,
    exception=GeminiCliVersionMismatch,
)


# The oldest gemini-cli metaproc can drive at all. The adapter passes --skip-trust,
# which 0.40 introduced; an older CLI rejects the flag with "Unknown arguments" and
# every agent step dies mid-run with no hint of the cause. Below this line the run is
# guaranteed to fail, so failing fast with the remedy beats a warning nobody reads --
# an operator lost a run to a stale 0.34.0 shadowing the pinned binary on PATH.
MIN_GEMINI_CLI_VERSION = "0.40.0"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _below_minimum(found: str, minimum: str) -> bool:
    """Whether ``found`` is older than ``minimum``, comparing component-wise.

    Both sides are zero-padded to the same width first, because a bare tuple compare
    reads ``0.40`` as older than ``0.40.0``. This gate stops runs, so a version that is
    actually new enough must never trip it; an unreadable version is not "below".
    """
    found_parts = _version_tuple(found)
    if not found_parts:
        return False
    minimum_parts = _version_tuple(minimum)
    width = max(len(found_parts), len(minimum_parts))
    padded_found = found_parts + (0,) * (width - len(found_parts))
    padded_minimum = minimum_parts + (0,) * (width - len(minimum_parts))
    return padded_found < padded_minimum


def _gemini_version_drift() -> str | None:
    """Return a drift message if the on-PATH ``gemini`` mismatches the pin, else
    None. Non-blocking for drift *at or above the minimum*: that is surfaced as a
    prominent warning, not a hard error. A CLI **below the minimum** raises
    ``GeminiCliVersionMismatch`` instead, because the run cannot succeed -- the
    adapter's required flags do not exist there. Set
    ``METAPROC_SKIP_GEMINI_VERSION_CHECK=1`` to bypass entirely.
    """
    skip = MetaprocEnv.METAPROC_SKIP_GEMINI_VERSION_CHECK.read_str(default="").lower()
    if skip in ("1", "true", "yes"):
        return None
    drift = check_cli_version(_GEMINI_VERSION_SPEC)
    if drift is not None:
        match = re.search(r"actual='([^']+)'", drift)
        found = match.group(1) if match else ""
        resolved = shutil.which("gemini")
        if _below_minimum(found, MIN_GEMINI_CLI_VERSION):
            raise GeminiCliVersionMismatch(
                f"gemini-cli {found} at {resolved} is older than the minimum "
                f"{MIN_GEMINI_CLI_VERSION} metaproc can drive: the adapter passes "
                "--skip-trust, which that CLI rejects, so every agent step would fail "
                f"mid-run. Fix PATH to a gemini >= {MIN_GEMINI_CLI_VERSION} (pinned: "
                f"{PINNED_GEMINI_CLI_VERSION}) or install the pin: "
                f"{GEMINI_CLI_INSTALL_COMMAND}"
            )
    return drift


GEMINI_NATIVE_SETTINGS_SCOPES = ("user", "workspace")
GEMINI_DEFAULT_NATIVE_SETTINGS_SCOPE = "user"
"""Where the adapter hands Gemini its native settings when a config names no scope.

`user` gives each launch a private Gemini home. It works on every CLI the adapter can
drive and needs no working directory or root-owned file.
"""

_SYSTEM_SCOPE_REFUSAL = (
    "native_settings_scope 'system' is not supported: gemini-cli 0.60.0 and later load a "
    "system settings file only when it and every directory above it are owned by root and "
    "writable by neither group nor others, so the file Metaproc writes would be skipped "
    "with a warning and every setting in it lost. Remove the key to use the default "
    "'user' scope, a private Gemini home per launch, or set 'workspace'"
)


def native_settings_scope(merged_config: dict[str, object]) -> object:
    """Return the scope a config resolves to, which ``validate_config`` checks."""
    return merged_config.get("native_settings_scope", GEMINI_DEFAULT_NATIVE_SETTINGS_SCOPE)


_GEMINI_ALLOWED_KEYS = frozenset(
    {
        "append_system_prompt",
        "cache",
        "estimated_process_rss_bytes",
        "estimated_process_rss_mb",
        "host_max_concurrency",
        "initial_memory_budget_fraction",
        "max_budget_usd",
        "model",
        "native_settings",
        "native_settings_home_template",
        "native_settings_scope",
        "output_format",
        "permission_mode",
        "sandbox",
        "timeout_s",
        "tools",
        "verbose",
        "working_directory",
    }
)

_TEMP_FILES_LOCK = threading.Lock()
_temp_files_dir: tempfile.TemporaryDirectory[str] | None = None


def _materialize_temp_file(*, prefix: str, suffix: str, content: str) -> Path:
    """Return a process-scoped, content-addressed Gemini configuration file."""
    global _temp_files_dir
    digest = sha256(content.encode("utf-8")).hexdigest()
    with _TEMP_FILES_LOCK:
        if _temp_files_dir is None:
            _temp_files_dir = tempfile.TemporaryDirectory(prefix="metaproc-gemini-")
        path = Path(_temp_files_dir.name) / f"{prefix}{digest}{suffix}"
        if not path.exists():
            write_secret_text(path, content)
    return path


def _materialize_workspace_settings(directory: Path, content: str) -> None:
    """Publish native settings without overwriting operator-authored configuration.

    A trusted workspace's settings carry no ownership rule, unlike system settings.
    """
    settings_dir = directory / ".gemini"
    settings_path = settings_dir / "settings.json"
    if settings_dir.is_symlink():
        raise ValueError("Gemini workspace settings directory must not be a symlink")
    settings_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with mkdir_lock(settings_dir / ".metaproc-settings.lock", stale_after=None):
        if settings_path.is_symlink():
            raise ValueError("Gemini workspace settings file must not be a symlink")
        if settings_path.exists():
            if json.loads(settings_path.read_text(encoding="utf-8")) != json.loads(content):
                raise ValueError(
                    f"Refusing to replace existing Gemini workspace settings: {settings_path}"
                )
            return
        with temp_output_dir(
            dir=settings_dir, prefix=".metaproc-settings-", always_clean=True
        ) as stage:
            staged_settings = stage / "settings.json"
            write_secret_text(staged_settings, content)
            try:
                # A non-Metaproc writer does not take our lock. Linking publishes
                # complete owner-only bytes without replacing a file it just created.
                settings_path.hardlink_to(staged_settings)
            except FileExistsError as exc:
                raise ValueError(
                    f"Refusing to replace existing Gemini workspace settings: {settings_path}"
                ) from exc


def _copy_user_template(source_fd: int, destination: Path) -> None:
    """Copy regular template assets through anchored, no-follow descriptors."""
    for name in sorted(os.listdir(source_fd)):
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"Gemini home template contains a symlink: {name}")
        directory = stat.S_ISDIR(metadata.st_mode)
        if not directory and not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"Gemini home template contains a non-regular asset: {name}")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        child_fd = os.open(name, flags, dir_fd=source_fd)
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError(f"Gemini home template changed while copying: {name}")
            target = destination / name
            if directory:
                target.mkdir(mode=0o700)
                _copy_user_template(child_fd, target)
            else:
                # write-contract: private-staging -- home is exposed only after the copy succeeds.
                with target.open("xb") as output, os.fdopen(os.dup(child_fd), "rb") as source:
                    target.chmod(0o600 | (stat.S_IMODE(opened.st_mode) & 0o100))
                    shutil.copyfileobj(source, output)
        finally:
            os.close(child_fd)


def _template_settings(settings_path: Path) -> dict[str, object]:
    if not settings_path.is_file():
        raise ValueError("Gemini home template requires .gemini/settings.json")
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini home template settings.json must be a JSON object") from exc
    if not isinstance(settings, dict):
        raise ValueError("Gemini home template settings.json must be a JSON object")
    return cast("dict[str, object]", settings)


def _materialize_user_settings(template: Path | None, native_settings: dict[str, object]) -> Path:
    """Prepare a private home; never borrow the operator's mutable runtime state.

    Without a template the home holds only ``.gemini/settings.json`` with the native
    settings, so Gemini takes its authentication from the launch environment.
    """
    global _temp_files_dir
    source_fd: int | None = None
    if template is not None:
        if (
            not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd
            or os.listdir not in os.supports_fd
        ):
            raise ValueError("Gemini home templates require no-follow directory descriptor support")
        if template.is_symlink():
            raise ValueError("Gemini home template must not be a symlink")
        source_fd = os.open(template, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with _TEMP_FILES_LOCK:
            if _temp_files_dir is None:
                _temp_files_dir = tempfile.TemporaryDirectory(prefix="metaproc-gemini-")
            home = Path(tempfile.mkdtemp(prefix="home-", dir=_temp_files_dir.name))
        try:
            settings_path = home / ".gemini" / "settings.json"
            settings: dict[str, object] = {}
            if source_fd is None:
                settings_path.parent.mkdir(mode=0o700)
            else:
                _copy_user_template(source_fd, home)
                settings = _template_settings(settings_path)
            merged = _deep_merge_settings(settings, native_settings)
            write_secret_text(settings_path, json.dumps(merged, sort_keys=True))
            return home
        except BaseException:
            # The caller never receives a partial home, including on cancellation.
            shutil.rmtree(home)
            raise
    finally:
        if source_fd is not None:
            os.close(source_fd)


def _deep_merge_settings(
    base: dict[str, object],
    override: dict[str, object],
) -> dict[str, object]:
    """Return ``base`` with ``override`` layered on top, recursing into dicts.

    Gemini native settings are merged rather than replaced so that a
    profile-supplied ``native_settings`` block cannot silently drop a default
    it never mentioned. That matters most for
    ``general.sessionRetention.enabled``, which is a host-safety setting: see
    ``GEMINI_SESSION_RETENTION_SETTINGS``. An operator who deliberately sets
    the key still wins, because an explicit override is layered last.
    """
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_settings(
                cast("dict[str, object]", existing),
                cast("dict[str, object]", value),
            )
        else:
            merged[key] = value
    return merged


def _build_gemini_flags(
    merged_config: dict[str, object],
    variables: dict[str, str],
) -> list[str]:
    """Build CLI flags for the stdin-streamed headless ``gemini`` invocation."""
    flags: list[str] = []

    model_str = _resolved_gemini_model(merged_config)
    flags.extend(["-m", model_str])

    permission_mode = merged_config.get("permission_mode")
    if permission_mode == "bypassPermissions":
        flags.extend(["--approval-mode", "yolo"])

    output_format = str(merged_config.get("output_format") or "stream-json")
    flags.extend(["--output-format", output_format])

    # gemini-cli 0.40+ added a workspace-trust gate that exits 55 when the
    # CLI is invoked from an untrusted directory. Every metaproc invocation
    # is headless, so the interactive trust prompt would deadlock; always
    # opt in via --skip-trust. (Operator's interactive `gemini` invocations
    # are unaffected — they use a different binary launch outside metaproc.)
    flags.append("--skip-trust")

    # gemini-cli's read_file tool refuses to read files outside cwd unless
    # the parent directory is in --include-directories. A workflow writes
    # per-item artifacts under <RUNS_DIR>/<RUN_ID>/, which is typically
    # outside the process cwd. Include the run directory so later steps can
    # read artifacts produced by earlier steps.
    runs_dir = variables.get("RUNS_DIR")
    run_id = variables.get("RUN_ID")
    if runs_dir and run_id:
        run_dir = f"{runs_dir.rstrip('/')}/{run_id}"
        flags.extend(["--include-directories", run_dir])

    sandbox = merged_config.get("sandbox")
    if sandbox:
        flags.extend(["-s", str(sandbox)])

    return flags


def _resolved_gemini_model(merged_config: dict[str, object]) -> str:
    """Return the exact model name the adapter will pass to Gemini CLI."""
    return resolve_model("gemini-cli", merged_config.get("model"))


def _token_count(value: object) -> int | None:
    """Return *value* as a token count, or None when it is not a non-negative integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _billed_tokens(entry: object) -> int:
    """Return the tokens one `result.stats.models` entry billed.

    Gemini CLI reports each model's `total_tokens` (input, output, and reasoning
    together). An entry without it counts its `input_tokens` plus `output_tokens`.
    """
    if not isinstance(entry, dict):
        return 0
    counts = cast("dict[str, object]", entry)
    total = _token_count(counts.get("total_tokens"))
    if total is not None:
        return total
    input_tokens = _token_count(counts.get("input_tokens")) or 0
    output_tokens = _token_count(counts.get("output_tokens")) or 0
    return input_tokens + output_tokens


def served_models(event: dict[str, object]) -> dict[str, int]:
    """Return the models a terminal `result` event says billed the call, with their tokens.

    `stats.models` lists every model the CLI sent a request to, and a request that
    failed still gets an entry, with zero tokens. A zero-token entry is a model that
    was tried, not one that answered, so it is left out: a silent fallback from the
    requested model then shows up as the fallback alone. Several models can bill one
    call, because utility and sub-agent requests go to other models. The run path's
    `validate_result_event` and `auth-check --assert-model` both decide which model
    served through this function, so they apply one rule.
    """
    stats = event.get("stats")
    models = cast("dict[str, object]", stats).get("models") if isinstance(stats, dict) else None
    if not isinstance(models, dict):
        return {}
    billed: dict[str, int] = {}
    for name, entry in cast("dict[str, object]", models).items():
        tokens = _billed_tokens(entry)
        if name and tokens > 0:
            billed[name] = tokens
    return billed


def format_served_models(served: dict[str, int]) -> str:
    """Render *served* as `model (N tokens)` items, the largest bill first."""
    ranked = sorted(served.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{name} ({tokens} tokens)" for name, tokens in ranked)


class GeminiCliAdapter:
    """Adapter for Gemini CLI (``gemini``)."""

    adapter_type: str = "gemini-cli"
    short_name: str = "gemini-cli"
    default_model: str | None = GEMINI_DEFAULT_MODEL

    def preflight(self) -> str | None:
        # Plan-time prerequisite check: surface any `gemini` version drift at
        # launch (as a prominent warning, not a block) rather than mid-DAG.
        return _gemini_version_drift()

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        _gemini_version_drift()
        flags = _build_gemini_flags(merged_config, variables)
        # Gemini treats `@path` as a model-facing read request, where workspace ignore
        # rules apply. Its headless mode natively accepts piped input, so keep the
        # durable prompt file for audit while streaming that content into the CLI.
        return [
            "/bin/sh",
            "-c",
            'prompt_file=$1; shift; exec "$@" < "$prompt_file"',
            "metaproc-gemini",
            str(prompt_file),
            "gemini",
            *flags,
        ]

    def validate_config(self, merged_config: dict[str, object]) -> list[ConfigRejection]:
        rejections: list[ConfigRejection] = []
        for key, value in merged_config.items():
            if key == "no_session_persistence":
                # Previously accepted and silently ignored: gemini-cli has no
                # headless flag that disables session recording, so the key
                # promised isolation the adapter never delivered. Reject it
                # rather than let a process spec believe it took effect.
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=(
                            "'no_session_persistence' is not supported by gemini-cli: it "
                            "has no flag that disables session recording. Metaproc always "
                            "disables Gemini's startup session-retention scan via native "
                            "settings; remove this key"
                        ),
                    )
                )
                continue
            if key not in _GEMINI_ALLOWED_KEYS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"{key!r} is not supported by gemini-cli")
                )
                continue
            if key == "model" and str(value) not in GEMINI_VALID_MODELS:
                rejections.append(
                    ConfigRejection(
                        key=key,
                        reason=f"unknown gemini-cli model {value!r}",
                    )
                )
        scope = native_settings_scope(merged_config)
        if scope == "system":
            rejections.append(
                ConfigRejection(key="native_settings_scope", reason=_SYSTEM_SCOPE_REFUSAL)
            )
        elif scope not in GEMINI_NATIVE_SETTINGS_SCOPES:
            rejections.append(
                ConfigRejection(
                    key="native_settings_scope",
                    reason="native_settings_scope must be 'user' or 'workspace'",
                )
            )
        if scope == "workspace" and not merged_config.get("working_directory"):
            rejections.append(
                ConfigRejection(
                    key="native_settings_scope",
                    reason="native_settings_scope 'workspace' requires working_directory",
                )
            )
        if merged_config.get("native_settings_home_template") and scope != "user":
            rejections.append(
                ConfigRejection(
                    key="native_settings_home_template",
                    reason="native_settings_home_template requires native_settings_scope 'user'",
                )
            )
        return rejections

    def prepare_env(
        self,
        env: dict[str, str],
        merged_config: dict[str, object],
    ) -> dict[str, str]:
        env = dict(env)
        append_system_prompt = merged_config.get("append_system_prompt")
        if append_system_prompt:
            env["GEMINI_SYSTEM_MD"] = str(
                _materialize_temp_file(
                    prefix="system-",
                    suffix=".md",
                    content=str(append_system_prompt),
                )
            )
        # Layer any profile-supplied block over the defaults rather than
        # replacing them, so an override that never mentions session retention
        # keeps the startup-scan mitigation. Re-assert the safety key beneath
        # the defaults so it survives even a caller that passes an empty or
        # partial `general` block.
        override = merged_config.get("native_settings")
        native_settings: dict[str, object] = _deep_merge_settings(
            _deep_merge_settings(
                GEMINI_SESSION_RETENTION_SETTINGS,
                GEMINI_DYNAMIC_MODEL_SETTINGS,
            ),
            GEMINI_DEFAULT_NATIVE_SETTINGS,
        )
        if isinstance(override, dict):
            native_settings = _deep_merge_settings(
                native_settings,
                cast("dict[str, object]", override),
            )
        scope = native_settings_scope(merged_config)
        if scope == "workspace":
            directory = self.working_directory(merged_config)
            if directory is None:
                raise ValueError("native_settings_scope 'workspace' requires working_directory")
            _materialize_workspace_settings(
                directory, json.dumps(native_settings, sort_keys=True, separators=(",", ":"))
            )
        elif scope == "user":
            template = merged_config.get("native_settings_home_template")
            env["GEMINI_CLI_HOME"] = str(
                _materialize_user_settings(
                    Path(str(template)) if template else None, native_settings
                )
            )
            # Preserve inherited system policy. This scope creates no system override.
        elif scope == "system":
            raise ValueError(_SYSTEM_SCOPE_REFUSAL)
        else:
            raise ValueError("native_settings_scope must be 'user' or 'workspace'")
        return env

    def working_directory(self, merged_config: dict[str, object]) -> Path | None:
        value = merged_config.get("working_directory")
        return Path(str(value)) if value else None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        return parse_jsonl_event(line, "result")

    def validate_result_event(
        self,
        event: dict[str, object],
        merged_config: dict[str, object],
    ) -> str | None:
        """Reject a successful result the requested model did not bill tokens for."""
        if event.get("status") != "success":
            return None

        expected = _resolved_gemini_model(merged_config)
        served = served_models(event)
        if expected in served:
            return None

        reported = format_served_models(served) if served else "no model that billed tokens"
        return (
            "Gemini terminal result contract violation: "
            f"requested model {expected!r}, but result.stats.models reported {reported}. "
            "Refusing the result because model substitution makes its provenance invalid."
        )

    def check_auth(self) -> AuthStatus:
        cli_path = shutil.which("gemini")
        gemini_api_key = MetaprocEnv.GEMINI_API_KEY.read_str(default=None)
        vertex_ai = MetaprocEnv.GOOGLE_GENAI_USE_VERTEXAI.read_bool(default=False)

        if not cli_path:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=False,
                cli_path=None,
                credentials_found=False,
                auth_mode="none",
                details="gemini CLI not found on PATH",
                setup_hint=GEMINI_CLI_INSTALL_HINT,
            )

        if gemini_api_key:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="gemini-api-key",
                details="GEMINI_API_KEY is set",
                setup_hint="",
            )

        if vertex_ai:
            google_api_key = MetaprocEnv.GOOGLE_API_KEY.read_str(default=None)
            has_project = bool(MetaprocEnv.GOOGLE_CLOUD_PROJECT.read_str(default=None))
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=bool(google_api_key) or has_project,
                auth_mode="vertex-ai-express" if google_api_key else "vertex-ai",
                details="Vertex AI mode configured",
                setup_hint="",
            )

        return AuthStatus(
            adapter_type=self.adapter_type,
            cli_found=True,
            cli_path=cli_path,
            credentials_found=False,
            auth_mode="none",
            details="No credentials configured",
            setup_hint=(
                "Pick one auth mode: "
                "(1) export GEMINI_API_KEY=AIza... for the direct API, or "
                "(2) export GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<project> "
                "for Vertex AI + ADC (reuses `gcloud auth application-default login`). "
                "See src/metaproc/docs/credential-setup.runbook.md#gemini-cli for all three modes."
            ),
        )

    def auth_info(self) -> str:
        return (
            "Gemini CLI auth modes:\n"
            "\n"
            "  1. AI Studio API key: export GEMINI_API_KEY=AIza...\n"
            "  2. Vertex AI express: export GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_API_KEY\n"
            "  3. Personal OAuth: gemini (interactive login)\n"
        )

    def bootstrap(self, home: Path) -> None:  # pyright: ignore[reportUnusedParameter]
        return None
