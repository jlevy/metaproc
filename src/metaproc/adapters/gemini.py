"""Gemini CLI adapter."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

from metaproc.adapters.base import AuthStatus, ConfigRejection, parse_jsonl_event
from metaproc.adapters.cli_version import (
    CliVersionMismatch,
    CliVersionSpec,
    check_cli_version,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.settings import (
    GEMINI_DEFAULT_MODEL,
    GEMINI_DEFAULT_NATIVE_SETTINGS,
    GEMINI_VALID_MODELS,
)

log = logging.getLogger(__name__)

PINNED_GEMINI_CLI_VERSION = "0.40.1"
GEMINI_CLI_INSTALL_HINT = f"Install: npm install -g @google/gemini-cli@{PINNED_GEMINI_CLI_VERSION}"


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
                f"{PINNED_GEMINI_CLI_VERSION}) or upgrade: "
                "npm install -g @google/gemini-cli"
            )
    return drift


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
        "no_session_persistence",
        "output_format",
        "permission_mode",
        "sandbox",
        "timeout_s",
        "tools",
        "verbose",
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
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)
    return path


def _build_gemini_flags(
    merged_config: dict[str, object],
    variables: dict[str, str],
) -> list[str]:
    """Build CLI flags for ``gemini -p`` from merged config."""
    flags: list[str] = []

    model = merged_config.get("model") or GEMINI_DEFAULT_MODEL
    model_str = str(model)
    if model_str in GEMINI_VALID_MODELS:
        flags.extend(["-m", model_str])
    else:
        log.warning(
            "gemini-cli: ignoring unknown model name %r; falling back to default %r",
            model_str,
            GEMINI_DEFAULT_MODEL,
        )
        flags.extend(["-m", GEMINI_DEFAULT_MODEL])

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
        native_settings = merged_config.get("native_settings", GEMINI_DEFAULT_NATIVE_SETTINGS)
        if native_settings:
            content = json.dumps(native_settings, sort_keys=True, separators=(",", ":"))
            env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(
                _materialize_temp_file(
                    prefix="settings-",
                    suffix=".json",
                    content=content,
                )
            )
        return env

    def working_directory(self, _merged_config: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        return parse_jsonl_event(line, "result")

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
