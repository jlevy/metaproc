"""Gemini CLI adapter."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

from metaproc.adapters.base import AuthStatus, ConfigRejection, parse_jsonl_event
from metaproc.config.env_vars import MetaprocEnv
from metaproc.settings import (
    GEMINI_DEFAULT_MODEL,
    GEMINI_DEFAULT_NATIVE_SETTINGS,
    GEMINI_VALID_MODELS,
)

log = logging.getLogger(__name__)

PINNED_GEMINI_CLI_VERSION = "0.40.1"
GEMINI_CLI_INSTALL_HINT = f"Install: npm install -g @google/gemini-cli@{PINNED_GEMINI_CLI_VERSION}"

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
    # the parent directory is in --include-directories. a workflow writes per-item
    # artifacts under <RUNS_DIR>/<RUN_ID>/, which is typically outside the
    # process's cwd (the consumer workflow repo root). Without this flag, downstream
    # agent steps that read prior step outputs (e.g. edge-candidate-ledger
    # reading market-timeline-priced.md) fail with "Path not in workspace".
    # See 2026-05-26 Tue AMC batch logbook [Services-4] for the incident.
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

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        flags = _build_gemini_flags(merged_config, variables)
        prompt_arg = prompt_file.read_text() if prompt_file.exists() else f"@{prompt_file}"
        return ["gemini", "-p", prompt_arg, *flags]

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
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                prefix="gemini-system-",
                delete=False,
            ) as tmp:
                tmp.write(str(append_system_prompt))
                env["GEMINI_SYSTEM_MD"] = tmp.name
        native_settings = merged_config.get("native_settings", GEMINI_DEFAULT_NATIVE_SETTINGS)
        if native_settings:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="gemini-settings-",
                delete=False,
            ) as tmp:
                json.dump(native_settings, tmp)
                env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = tmp.name
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
                "See docs/runbooks/credential-setup.runbook.md#gemini-cli for all three modes."
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
