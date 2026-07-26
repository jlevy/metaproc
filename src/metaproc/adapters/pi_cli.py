"""Pi CLI adapter — JSON mode (Phase 2b).

Provider metadata (env var names, labels, credential detection) lives in
`metaproc/config/providers.py`. To add a new provider, edit that file —
this adapter discovers new providers automatically via
`providers_with_api_keys()`.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, cast

from metaproc.adapters.base import (
    AuthStatus,
    ConfigRejection,
    parse_jsonl_event,
    resolve_templates,
)
from metaproc.cloud.gcp.resolve_token import resolve_gcp_token
from metaproc.config.env_vars import MetaprocEnv
from metaproc.config.providers import providers_with_api_keys
from metaproc.settings import (
    PI_DEFAULT_MODEL,
    PI_DEFAULT_PROVIDER,
    PI_VALID_MODELS,
    PI_VALID_PROVIDERS,
)

log = logging.getLogger(__name__)

PINNED_PI_CODING_AGENT_VERSION = "0.72.1"
PI_CLI_INSTALL_HINT = (
    f"Install: npm install -g @mariozechner/pi-coding-agent@{PINNED_PI_CODING_AGENT_VERSION}"
)

_PI_ALLOWED_KEYS = frozenset(
    {
        "api_key",
        "append_system_prompt",
        "cache",
        "estimated_process_rss_bytes",
        "estimated_process_rss_mb",
        "host_max_concurrency",
        "initial_memory_budget_fraction",
        "max_budget_usd",
        "model",
        "no_session_persistence",
        "output_format",
        "provider",
        "thinking",
        "timeout_s",
        "tool_choice",
        "tools",
        "verbose",
    }
)

# FIXME: Replace per-adapter tool maps with abstract capability definitions
# (fs.read, fs.write, shell, web.search, etc.) as proposed in rev3 design
# Section 13.1. Process specs should declare capabilities; adapters declare
# which capabilities they support and how they map to vendor tool names.
# See: docs/project/design/metaprocess-framework-design-draft-rev3.md

# Map canonical (Claude-style) tool names to Pi equivalents.
# Tools without a Pi equivalent are silently dropped.
_TOOL_MAP: dict[str, str] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Bash": "bash",
    "Grep": "grep",
    "Glob": "find",
}

PI_AVAILABLE_TOOLS = frozenset({"read", "bash", "edit", "write", "grep", "find", "ls"})


def _build_pi_flags(
    merged_config: dict[str, object],
    variables: dict[str, str],
) -> list[str]:
    """Build CLI flags for ``pi --mode json -p`` from merged config."""
    flags: list[str] = []

    # Provider
    provider = merged_config.get("provider") or PI_DEFAULT_PROVIDER
    provider_str = str(provider)
    if provider_str in PI_VALID_PROVIDERS:
        flags.extend(["--provider", provider_str])
    else:
        log.warning(
            "pi-cli: unknown provider %r; falling back to %r",
            provider_str,
            PI_DEFAULT_PROVIDER,
        )
        flags.extend(["--provider", PI_DEFAULT_PROVIDER])

    # Model
    model = merged_config.get("model") or PI_DEFAULT_MODEL
    model_str = str(model)
    if model_str in PI_VALID_MODELS:
        flags.extend(["--model", model_str])
    else:
        log.warning(
            "pi-cli: unknown model %r; falling back to %r",
            model_str,
            PI_DEFAULT_MODEL,
        )
        flags.extend(["--model", PI_DEFAULT_MODEL])

    # Thinking level
    thinking = merged_config.get("thinking")
    if thinking:
        flags.extend(["--thinking", str(thinking)])

    # Tools — map canonical names to Pi equivalents, drop unsupported
    tools = merged_config.get("tools")
    if tools:
        if isinstance(tools, list):
            mapped: list[str] = []
            for t in cast("list[Any]", tools):
                name = str(t)
                pi_name = _TOOL_MAP.get(name, name.lower())
                if pi_name in PI_AVAILABLE_TOOLS:
                    mapped.append(pi_name)
                else:
                    log.debug("pi-cli: dropping unsupported tool %r", name)
            if mapped:
                # Deduplicate while preserving order
                seen: set[str] = set()
                unique: list[str] = []
                for x in mapped:
                    if x not in seen:
                        seen.add(x)
                        unique.append(x)
                flags.extend(["--tools", ",".join(unique)])
        else:
            flags.extend(["--tools", str(tools)])

    # System prompt
    append_system_prompt = merged_config.get("append_system_prompt")
    if append_system_prompt:
        resolved = resolve_templates(str(append_system_prompt), variables)
        flags.extend(["--append-system-prompt", resolved])

    # API key override (e.g. pre-resolved gcloud token)
    api_key = merged_config.get("api_key")
    if api_key:
        flags.extend(["--api-key", str(api_key)])

    # tool_choice: recognized at the adapter layer so process specs can declare
    # intent (notably pi-qwen3-* variants, which the scaling plan wants to run
    # with tool_choice=auto for function-calling accuracy). Pi-cli's pi-ai
    # provider library already supports the option, but as of 2026-04-17 the
    # pi-cli binary does not expose a --tool-choice flag (or equivalent env
    # var) that routes it into stream options — confirmed by auditing the
    # installed pi-coding-agent dist at /opt/node22/lib/.../dist/core/. We
    # record the declaration and a one-shot warning; when pi-cli adds the
    # flag upstream, flip the log call below to flags.extend([...]).
    tool_choice = merged_config.get("tool_choice")
    if tool_choice:
        log.warning(
            "pi-cli: tool_choice=%r declared but pi-cli does not yet expose it "
            "via CLI; intent recorded, invocation proceeds without the override",
            tool_choice,
        )

    # Verbose
    if merged_config.get("verbose"):
        flags.append("--verbose")

    return flags


_pi_binary_cache: str | None = None


def resolve_pi_binary() -> str:
    """Public wrapper around the cached pi-binary resolver."""
    return _resolve_pi_binary()


def _resolve_pi_binary() -> str:
    """Resolve the absolute path of the ``pi`` CLI binary.

    Cached for the process lifetime. Raises FileNotFoundError with a
    diagnostic message (listing directories searched and the effective
    PATH) if the binary can't be found — container bootstrap issues on
    GCP Batch workers were previously surfacing as opaque
    ``FileNotFoundError: 'pi'`` from asyncio subprocess execvp with no
    way to tell whether PATH was wrong or the binary was genuinely
    missing.
    """
    global _pi_binary_cache  # noqa: PLW0603
    if _pi_binary_cache is not None:
        return _pi_binary_cache
    found = shutil.which("pi")
    if not found:
        candidates = [
            "/usr/local/bin/pi",
            "/usr/bin/pi",
            "/opt/venv/bin/pi",
            str(Path.home() / ".local" / "bin" / "pi"),
        ]
        for cand in candidates:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                found = cand
                break
    if not found:
        path_env = os.environ.get("PATH", "<unset>")
        raise FileNotFoundError(
            "pi CLI binary not found via PATH or known install locations. "
            f"PATH={path_env}. "
            f"{PI_CLI_INSTALL_HINT}"
        )
    _pi_binary_cache = found
    log.info("pi-cli: resolved pi binary to %s", found)
    return found


def resolve_gcloud_token() -> str:
    """Return a valid GCP access token — delegates to cloud.gcp.resolve_token."""

    return resolve_gcp_token()


class PiCliAdapter:
    """Adapter for Pi coding agent CLI (``pi``) — JSON mode (Phase 2b)."""

    adapter_type: str = "pi-cli"
    short_name: str = "pi-cli"
    default_model: str | None = PI_DEFAULT_MODEL

    def build_command(
        self,
        prompt_file: Path,
        merged_config: dict[str, object],
        variables: dict[str, str],
    ) -> list[str]:
        flags = _build_pi_flags(merged_config, variables)
        pi_binary = _resolve_pi_binary()
        return [pi_binary, "--mode", "json", "-p", f"@{prompt_file}", "--no-session", *flags]

    def validate_config(self, merged_config: dict[str, object]) -> list[ConfigRejection]:
        rejections: list[ConfigRejection] = []
        for key, value in merged_config.items():
            if key not in _PI_ALLOWED_KEYS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"{key!r} is not supported by pi-cli")
                )
                continue
            if key == "provider" and str(value) not in PI_VALID_PROVIDERS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"unknown pi-cli provider {value!r}")
                )
            if key == "model" and str(value) not in PI_VALID_MODELS:
                rejections.append(
                    ConfigRejection(key=key, reason=f"unknown pi-cli model {value!r}")
                )
        return rejections

    def prepare_env(
        self,
        env: dict[str, str],
        _merged_config: dict[str, object],
    ) -> dict[str, str]:
        env = dict(env)
        # Skip version check to avoid startup delays
        env["PI_SKIP_VERSION_CHECK"] = "1"
        return env

    def working_directory(self, _merged_config: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, line: str) -> dict[str, object] | None:
        """Detect ``agent_end`` event in Pi JSON mode JSONL output."""
        return parse_jsonl_event(line, "agent_end")

    def check_auth(self) -> AuthStatus:
        cli_path = shutil.which("pi")

        if not cli_path:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=False,
                cli_path=None,
                credentials_found=False,
                auth_mode="none",
                details="pi CLI not found on PATH",
                setup_hint=PI_CLI_INSTALL_HINT,
            )

        # Pi credential resolution order:
        # 1. --api-key CLI override (not checked here — runtime only)
        # 2. auth.json (stored credentials from `pi /login`)
        # 3. Environment variables per provider
        # 4. Custom provider keys

        # Check for auth.json (stored credentials from `pi /login`)
        auth_json = Path.home() / ".pi" / "agent" / "auth.json"
        if auth_json.exists():
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode="auth-json",
                details=f"Stored credentials found at {auth_json}",
                setup_hint="",
            )

        # Check for provider API keys via the central provider registry.
        # Adding a new provider's env var should be a one-line edit to
        # metaproc/config/providers.py — never to this loop.
        providers_found: list[str] = [
            spec.name for spec in providers_with_api_keys() if spec.credentials_present()
        ]
        if MetaprocEnv.GOOGLE_CLOUD_PROJECT.read_str(default=None):
            providers_found.append("vertex-ai")

        if providers_found:
            return AuthStatus(
                adapter_type=self.adapter_type,
                cli_found=True,
                cli_path=cli_path,
                credentials_found=True,
                auth_mode=f"api-key ({', '.join(providers_found)})",
                details=f"Credentials found for: {', '.join(providers_found)}",
                setup_hint="",
            )

        env_var_names = sorted(
            spec.api_key_env.name
            for spec in providers_with_api_keys()
            if spec.api_key_env is not None
        )
        return AuthStatus(
            adapter_type=self.adapter_type,
            cli_found=True,
            cli_path=cli_path,
            credentials_found=False,
            auth_mode="none",
            details="No provider credentials found",
            setup_hint=(
                "Set one of: "
                + ", ".join(env_var_names)
                + "; or run `pi /login` to store credentials"
            ),
        )

    def auth_info(self) -> str:
        lines = [
            "Pi CLI auth modes (checked in order):",
            "",
            "  1. auth.json:  run `pi /login` to store credentials",
        ]
        idx = 2
        for spec in providers_with_api_keys():
            assert spec.api_key_env is not None  # narrow for type-checker
            lines.append(f"  {idx}. {spec.label:<10} export {spec.api_key_env.name}=...")
            idx += 1
        lines.append(f"  {idx}. Vertex AI:  export GOOGLE_CLOUD_PROJECT=...")
        return "\n".join(lines) + "\n"

    def bootstrap(self, home: Path) -> None:  # pyright: ignore[reportUnusedParameter]
        return None
