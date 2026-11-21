"""metaproc auth-check — verify adapters, credentials, and optionally live connectivity.

Phase 1: adapter binaries, credentials, disk space, gcloud token.
Phase 2 (--live): send a trivial prompt to each configured model and report latency.
Phase 3 (--run-dir): verify dataset/progress file, output dir, item counts.

Design reference: plan-2026-04-01-mine-batch-scaling.md §5.2.
Pattern reference: an external tool dev api-health (ApiHealthHandler in dev.ts).
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import typer
from strif import temp_output_file

from metaproc.adapters.base import AGENT_ENV_OVERRIDES, agent_seed_env, parse_jsonl_event
from metaproc.adapters.codex_cli import codex_rollout_model, codex_thread_id, resolve_codex_home
from metaproc.adapters.gemini_cli import GEMINI_ALIAS_SERVED_MODELS, format_served_models
from metaproc.adapters.gemini_cli import served_models as gemini_served_models
from metaproc.adapters.pi_cli import resolve_pi_binary
from metaproc.adapters.registry import ADAPTER_REGISTRY, get_auth_capable
from metaproc.cli import app, get_output
from metaproc.config.env_vars import MetaprocEnv
from metaproc.config.model_catalog import resolve_model
from metaproc.config.providers import infer_provider, provider_by_name
from metaproc.dispatch.slot_coordinator import apply_slot_env
from metaproc.engine.preflight import check_disk_space
from metaproc.engine.resource_sampling import run_command_in_process_group
from metaproc.errors import CLIError
from metaproc.io import atomic_write_text, fmf_read_frontmatter
from metaproc.models.execution_profile import ExecutionProfileRegistry

log = logging.getLogger(__name__)


_ISOLATED_PROBE_ENV_KEYS = frozenset(
    {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP", *AGENT_ENV_OVERRIDES}
)


def _isolated_probe_env(env: Mapping[str, str], *extra_keys: str) -> dict[str, str]:
    return {key: env[key] for key in _ISOLATED_PROBE_ENV_KEYS.union(extra_keys) if key in env}


def _check_gcloud_token() -> tuple[bool, str]:
    """Check that a GCP access token can be resolved via google.auth."""
    try:
        from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            resolve_gcp_token,
        )

        token = resolve_gcp_token()
    except ImportError:
        return False, "GCP token: metaproc[gcp] not installed (optional — needed for Vertex MaaS)"
    except Exception as exc:  # noqa: BLE001
        return False, f"GCP token: error — {exc}"

    from metaproc.cloud.gcp.gcp_credentials import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
        get_token_expiry,
    )

    expiry = get_token_expiry()
    return True, f"GCP token: ok via google.auth (len={len(token)}, expiry={expiry})"


def _check_adapter(adapter_type: str, *, live: bool = False) -> list[tuple[bool | None, str]]:
    """Run credential checks for a single adapter."""
    adapter = ADAPTER_REGISTRY[adapter_type]
    status = adapter.check_auth()
    results: list[tuple[bool | None, str]] = []

    # Binary check
    if status.cli_found:
        results.append((True, f"{adapter_type}: binary found at {status.cli_path}"))
    else:
        results.append((False, f"{adapter_type}: binary unavailable — {status.details}"))
        if status.setup_hint:
            results.append((False, f"  hint: {status.setup_hint}"))
        return results

    # Credentials check
    if status.credentials_found:
        results.append((True, f"{adapter_type}: credentials ok ({status.auth_mode})"))
    elif adapter_type == "codex-cli" and status.auth_mode == "unknown":
        next_step = "checking in the live probe" if live else "use --live to verify"
        results.append(
            (
                None if live else False,
                f"{adapter_type}: credentials unverified — {status.details}; {next_step}",
            )
        )
    else:
        results.append((False, f"{adapter_type}: no credentials found"))
        if status.setup_hint:
            results.append((False, f"  hint: {status.setup_hint}"))

    return results


def _find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


class _VariantTarget(NamedTuple):
    """What `--variant` names: an adapter, and for a profile its model, provider and config."""

    adapter: str | None
    model: str | None
    provider: str | None
    config: dict[str, object]


def _resolve_variant_target(
    variant: str | None,
    *,
    profile_files: list[Path] | tuple[Path, ...] = (),
) -> _VariantTarget:
    """Resolve a profile or raw adapter name to its adapter, model, provider and config.

    For an execution profile, `config` is a copy of the profile's own adapter config,
    the same block a run seeds each of the profile's steps from; a raw adapter name
    has none.
    """
    if not variant:
        return _VariantTarget(None, None, None, {})

    registry = ExecutionProfileRegistry.load(
        repo_root=_find_repo_root(Path.cwd()),
        explicit_files=tuple(profile_files),
    )
    if variant in registry.entries:
        resolved = registry.resolve(variant)
        profile = resolved.profile
        return _VariantTarget(
            profile.adapter,
            str(profile.config.get("model") or profile.model or "") or None,
            str(profile.config.get("provider") or profile.provider or "") or None,
            dict(profile.config),
        )

    if variant in ADAPTER_REGISTRY:
        return _VariantTarget(variant, None, None, {})

    available = ", ".join(registry.names) or "<none>"
    raise CLIError(f"unknown execution profile for --variant: {variant!r} (available: {available})")


def _infer_pi_provider(model_name: str | None) -> str | None:
    """Infer the pi-cli provider from a model name using the central
    provider registry.

    The live check needs to exercise the actual provider the operator asked
    for. Without an explicit provider, pi-cli silently falls back to its
    default and false-greens the live check against a different model.
    Provider name-prediction lives in `metaproc.config.providers.PROVIDERS`;
    extending it for a new provider is a one-place change.
    """
    spec = infer_provider(model_name)
    return spec.name if spec else None


def _pi_list_models(
    env: dict[str, str], timeout_s: int = 15, *, isolated: bool = False
) -> tuple[bool, str]:
    """Capture ``pi --list-models`` output under ``env``.

    Returns ``(ok, combined_output)``. ``ok`` is ``False`` only when the
    subprocess itself could not run (binary missing, timed out). A non-zero
    exit with stderr is still considered a successful capture so the caller
    can surface the error text as diagnostic.
    """

    try:
        pi_binary = resolve_pi_binary()
    except FileNotFoundError as exc:
        return False, str(exc)

    flags = ["--no-mcp", "--no-extensions"] if isolated else []
    try:
        proc = subprocess.run(
            [pi_binary, "--list-models", *flags],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            cwd=env["PI_CODING_AGENT_DIR"] if isolated else None,
        )
    except subprocess.TimeoutExpired:
        return False, f"pi --list-models timed out after {timeout_s}s"

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return True, combined


def _pi_validate_registration(
    provider: str | None,
    model: str | None,
    env: dict[str, str],
    timeout_s: int = 15,
    *,
    isolated: bool = False,
) -> tuple[bool, str]:
    """Verify that ``provider`` and ``model`` are registered with pi-cli.

    Runs ``pi --list-models`` under ``env`` and looks for the provider name
    and model ID in the output. This is the gate that stops
    ``auth-check --live`` from false-greening when pi-cli silently falls
    back to its default provider.
    """
    ok, output = _pi_list_models(env, timeout_s=timeout_s, isolated=isolated)
    if not ok:
        return False, output

    missing: list[str] = []
    if provider and provider not in output:
        missing.append(f"provider {provider!r}")
    if model and model not in output:
        missing.append(f"model {model!r}")
    if missing:
        return False, (
            "pi --list-models does not advertise "
            + " and ".join(missing)
            + " — the live check would exercise a different model than requested"
        )
    return True, "registration validated via pi --list-models"


_PI_GCP_TOKEN_ENV = "METAPROC_PI_API_KEY"
_MAX_PI_PROBE_CATALOG_BYTES = 4 * 1024 * 1024
_PI_GCP_TOKEN_SOURCES = frozenset(
    {
        "$METAPROC_PI_API_KEY",
        "${METAPROC_PI_API_KEY}",
        # The command in the packaged and Batch-rendered provider catalog.
        (
            '!sh -c \'if [ -n "$METAPROC_PI_API_KEY" ]; then '
            'printf "%s" "$METAPROC_PI_API_KEY"; else exec gcp-access-token.sh; fi\''
        ),
    }
)


def _stage_pi_gcp_probe_catalog(
    provider: str, requested_model: str, env: Mapping[str, str], stage_dir: Path
) -> str:
    """Stage one MaaS route with an explicit key source and no inherited Pi login."""
    agent_dir = env.get("PI_CODING_AGENT_DIR")
    home = env.get("HOME")
    models_path = (
        Path(agent_dir).expanduser()
        if agent_dir
        else (Path(home) if home else Path.home()) / ".pi" / "agent"
    ) / "models.json"
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(models_path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(_MAX_PI_PROBE_CATALOG_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > _MAX_PI_PROBE_CATALOG_BYTES:
            raise ValueError("exceeds the Pi probe catalog read limit")
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read {provider} probe catalog {models_path}: {exc}") from exc
    try:
        config = json.loads(data.decode("utf-8-sig"))["providers"][provider]
        api_key = config["apiKey"]
    except OSError, ValueError, KeyError, TypeError:
        config = {}
        api_key = None
    if not isinstance(api_key, str) or api_key not in _PI_GCP_TOKEN_SOURCES:
        raise ValueError(
            f'set providers.{provider}.apiKey in {models_path} to "${_PI_GCP_TOKEN_ENV}" '
            "or use Metaproc's packaged provider configuration"
        )

    base_url = config.get("baseUrl", "")
    try:
        parsed = urlsplit(base_url if isinstance(base_url, str) else "")
        trusted_url = (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.port in (None, 443)
            and re.fullmatch(r"(?:[a-z0-9-]+-)?aiplatform\.googleapis\.com", parsed.hostname or "")
        )
    except ValueError:
        trusted_url = False
    if not trusted_url or config.get("api") != "openai-completions":
        raise ValueError(f"{models_path} must route {provider} to an HTTPS Vertex AI endpoint")

    defs = config.get("models")
    matching = (
        [
            model
            for model in defs
            if isinstance(model, dict)
            and isinstance(model.get("id"), str)
            and (
                model["id"] == requested_model or model["id"].rsplit("/", 1)[-1] == requested_model
            )
        ]
        if isinstance(defs, list)
        else []
    )
    if len(matching) != 1:
        raise ValueError(
            f"{models_path} must define one {provider} model matching {requested_model!r}"
        )

    selected = matching[0]
    catalog = {
        "providers": {
            provider: {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": f"${_PI_GCP_TOKEN_ENV}",
                "authHeader": True,
                "models": [
                    {
                        key: value
                        for key, value in selected.items()
                        if key
                        in {
                            "id",
                            "name",
                            "reasoning",
                            "input",
                            "contextWindow",
                            "maxTokens",
                            "cost",
                        }
                    }
                ],
            }
        }
    }
    atomic_write_text(stage_dir / "models.json", json.dumps(catalog))
    return selected["id"]


def _resolve_pi_gcp_probe_token() -> str:
    """Mint a bearer in the parent using its application default credentials."""
    try:
        from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- optional [gcp] dependency
            resolve_gcp_token,
        )

        token = resolve_gcp_token()
    except ImportError:
        raise ValueError("install metaproc[gcp] to resolve a Vertex MaaS credential") from None
    except Exception as exc:  # noqa: BLE001 -- report any upstream credential failure safely
        raise ValueError(
            f"GCP access token unavailable ({type(exc).__name__}); check Application Default Credentials"
        ) from None
    if not token.strip():
        raise ValueError("GCP access token unavailable; check Application Default Credentials")
    return token


# Adapter-specific matchers for the identity event that carries model ID.
# Each entry is (event type or None, event subtype or None, dotted-path to
# model within the event). `None` for type means "any line" — used for
# the config-dump preamble older codex-cli releases printed, `{"model": ..., ...}`
# with no `type` field. The first JSONL line matching both type/subtype (when
# specified) and containing a non-empty string at *model_path* wins.
#
# An identity event is not billing accounting, so it need not name the model
# that answered: see `_SERVED_MODEL_READERS` below.
_MODEL_EVENT_MATCHERS: dict[str, tuple[str | None, str | None, tuple[str, ...]]] = {
    "claude-code-cli": ("system", "init", ("model",)),
    "gemini-cli": ("init", None, ("model",)),
    # pi-cli emits `agent_start` without model; the model ID lives under
    # `message.model` on the first `message_start` event emitted by the
    # assistant turn. Matching `message_start` directly gives us the
    # identity event even when agent_start is empty.
    "pi-cli": ("message_start", None, ("message", "model")),
    # Older codex-cli releases (0.124.0) printed an untyped config-dump line
    # first (e.g. `{"model":"gpt-5","provider":"openai",...}`). The pinned
    # 0.166.0 prints none under `--json`, and its typed stream carries no model
    # at all, so `_evaluate_codex_model_assertion` falls back to the session
    # rollout and then the argv.
    "codex-cli": (None, None, ("model",)),
}


def _extract_observed_model(adapter_type: str, stdout: str) -> str | None:
    """Scan a CLI's JSONL stdout for the identity event and return its model.

    Returns None when the adapter does not emit a model-carrying event,
    when the expected event never appeared, or when the stdout is empty /
    malformed.
    """
    matcher = _MODEL_EVENT_MATCHERS.get(adapter_type)
    if matcher is None:
        return None
    want_type, want_subtype, model_path = matcher

    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if want_type is not None and event.get("type") != want_type:
            continue
        if want_subtype is not None and event.get("subtype") != want_subtype:
            continue
        node: object = event
        for key in model_path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            return node
    return None


# Adapters whose terminal `result` event accounts for the models that billed the
# call, each mapped to the adapter's own reader of that accounting.
#
# Gemini CLI emits its `init` event with `config.getModel()` before any request
# leaves the process, so that event echoes the requested id even on a call the
# CLI rewrote to a different model — exactly the routing change `--assert-model`
# exists to catch. Its terminal `result` event keys `stats.models` by every model
# it sent a request to, and `gemini_cli.served_models` keeps the ones that billed
# tokens. `GeminiCliAdapter.validate_result_event` refuses a run's result through
# the same function, so the probe and the run path decide which model served by
# one rule, and they compare by one rule too: the asserted id must itself have
# billed, or, for a moving alias, one of the models `_SERVED_MODEL_ALIASES` says the
# pinned CLI serves it as. A substring match would pass `gemini-3.1-pro-preview`
# when `gemini-3.1-pro-preview-customtools` served, and could never pass `auto`.
_SERVED_MODEL_READERS: dict[str, Callable[[dict[str, object]], dict[str, int]]] = {
    "gemini-cli": gemini_served_models,
}

# Per adapter: moving alias -> the concrete models the pinned CLI may bill it as.
_SERVED_MODEL_ALIASES: dict[str, Mapping[str, frozenset[str]]] = {
    "gemini-cli": GEMINI_ALIAS_SERVED_MODELS,
}


def _extract_served_models(adapter_type: str, stdout: str) -> dict[str, int] | None:
    """Return the models a CLI's terminal event says billed the call, with their tokens.

    Returns None when the adapter reports no served-model accounting, or when
    *stdout* carries no terminal event at all. Returns an empty dict when the
    terminal event is present but no model in it billed tokens — a case the
    caller must report rather than pass, because nothing then establishes what
    answered.
    """
    reader = _SERVED_MODEL_READERS.get(adapter_type)
    if reader is None:
        return None

    for line in reversed(stdout.splitlines()):
        event = parse_jsonl_event(line, "result")
        if event is not None:
            return reader(event)
    return None


def _run_live_check(
    adapter_type: str,
    variant: str | None,
    timeout_s: int,
    provider: str | None = None,
    *,
    assert_model: str | None = None,
    profile_config: Mapping[str, object] | None = None,
) -> list[tuple[bool, str]]:
    """Send a trivial prompt to an adapter and report success + latency.

    *profile_config* is the adapter config of the execution profile `--variant`
    named, if any. The probe starts from it, as a run starts a step from it, and
    then sets what the probe itself needs.

    When *assert_model* is set, also parse the subprocess stdout and fail unless
    the model it names contains the expected substring (gemini-cli: equals it, or
    is one a moving alias is served as). Where that model is read differs by
    adapter:

    - gemini-cli: the terminal `result` event's `stats.models`, the models that
      billed the call, so a request the CLI rewrote or answered with a fallback
      fails.
    - claude-code-cli: `system.init.model`, the model Claude Code resolved the
      request to. Its `result.modelUsage` carries the models that billed but is
      not read yet.
    - pi-cli: `message.model` on the first assistant `message_start` event.
    - codex-cli: the model Codex *requested*: a legacy config preamble if one is
      printed, else the `turn_context` model in the thread's session rollout. With
      neither, the assertion fails as not verifiable; the probe's `-m` is not
      evidence. `codex exec` reports no model that served the call.
    """

    adapter = ADAPTER_REGISTRY[adapter_type]
    status = adapter.check_auth()
    can_probe_credentials = status.credentials_found or (
        adapter_type == "codex-cli" and status.auth_mode == "unknown"
    )
    if not status.cli_found or not can_probe_credentials:
        return [(False, f"{adapter_type}: skipping live check (no binary or credentials)")]

    # Start from the profile's own adapter config, as a run seeds a step's config
    # from it (`engine/build_plan.py`), so a profile setting that changes routing —
    # a Gemini `native_settings` block above all — reaches the probe. Without a
    # profile this is a minimal config, just enough for a trivial prompt. The
    # probe's own requirements are layered on top.
    # For pi-cli, pin the provider explicitly so the live check exercises the
    # requested provider rather than falling back to pi-cli's default — that
    # fallback is what produced the vertex-maas false-green (see Phase 0c
    # blocker report).
    merged_config: dict[str, object] = dict(profile_config or {})
    if variant is not None:
        merged_config["model"] = variant
    effective_provider = provider
    if adapter_type == "pi-cli" and variant and not effective_provider:
        effective_provider = _infer_pi_provider(variant)
    if adapter_type == "pi-cli" and effective_provider:
        merged_config["provider"] = effective_provider
    if adapter_type == "claude-code-cli":
        merged_config.setdefault("permission_mode", "bypassPermissions")
        # assert_model needs the `system.init` event, which only appears
        # under stream-json. Plain text output carries no model marker, so the
        # assertion overrides a profile's format rather than defaulting it.
        if assert_model:
            merged_config["output_format"] = "stream-json"
        else:
            merged_config.setdefault("output_format", "text")
        merged_config["verbose"] = merged_config["output_format"] == "stream-json"
        merged_config["no_session_persistence"] = True
    if adapter_type == "gemini-cli" and assert_model:
        # Same reasoning: need the terminal `result` event carrying
        # `stats.models`, which only stream-json emits. A profile whose
        # `output_format` is `text` would otherwise leave no terminal event.
        merged_config["output_format"] = "stream-json"
    if adapter_type == "codex-cli" and not any(
        merged_config.get(key) for key in ("permission_mode", "sandbox", "approval_policy")
    ):
        # Codex refuses to dispatch without a permission signal because it
        # would otherwise prompt for approval on every tool call and
        # deadlock in batch mode (stdin=DEVNULL). A trivial "Respond with
        # exactly: OK" prompt never executes tools, so bypassing approvals
        # is the minimum safe default for the live probe. A profile that
        # already gives a signal keeps it: bypass would override its sandbox.
        merged_config["permission_mode"] = "bypassPermissions"

    results: list[tuple[bool, str]] = []
    label = f"{adapter_type}" + (f" ({variant})" if variant else "")

    # Write a trivial prompt to a temp file
    with tempfile.NamedTemporaryFile(encoding="utf-8", mode="w", suffix=".md", delete=False) as f:
        f.write("Respond with exactly: OK")
        prompt_path = Path(f.name)

    pi_stage: tempfile.TemporaryDirectory[str] | None = None
    try:
        try:
            cmd = adapter.build_command(
                prompt_file=prompt_path,
                merged_config=merged_config,
                variables={},
            )
            # The live check never runs planning's validation, so a config the adapter
            # refuses at launch, such as Gemini's removed system scope, surfaces here.
            env = adapter.prepare_env(agent_seed_env(), merged_config)
        except ValueError as exc:
            return [(False, f"{label}: invalid adapter configuration: {exc}")]

        # Disable function-tools for pi-cli live smokes so the dispatch
        # exercises a clean roundtrip without hitting OpenAI's
        # `/v1/chat/completions` constraint that gpt-5.x reasoning models
        # reject `reasoning_effort + function tools` (operators must use
        # `/v1/responses` for that combo). The trivial "Respond with OK"
        # prompt does not need tools, and turning them off keeps the
        # smoke compatible across providers.
        if adapter_type == "pi-cli" and "--no-tools" not in cmd:
            cmd = [*cmd, "--no-tools"]

        # The registration command filters by credential availability, so both
        # Pi children need the key in their environment. Never put it in argv.
        pi_gcp_token: str | None = None
        registration_model = variant
        if adapter_type == "pi-cli" and effective_provider:
            spec = provider_by_name(effective_provider)
            if spec is not None and spec.requires_gcp_token:
                pi_stage = tempfile.TemporaryDirectory(prefix="metaproc-pi-maas-check-")
                try:
                    registration_model = _stage_pi_gcp_probe_catalog(
                        effective_provider,
                        resolve_model("pi-cli", merged_config.get("model")),
                        env,
                        Path(pi_stage.name),
                    )
                    pi_gcp_token = _resolve_pi_gcp_probe_token()
                except ValueError as exc:
                    return [(False, f"{label}: {exc}")]
                env = _isolated_probe_env(env, "HOME", "PI_SKIP_VERSION_CHECK", "PI_TELEMETRY")
                env["PI_CODING_AGENT_DIR"] = pi_stage.name
                env[_PI_GCP_TOKEN_ENV] = pi_gcp_token
                cmd = [*cmd, "--no-extensions"]

        # Pre-flight: confirm pi-cli actually knows the requested provider/model
        # before we dispatch a trivial prompt. Without this gate, pi-cli
        # silently falls back to its default provider and the live check
        # reports PASS against a completely different model.
        if adapter_type == "pi-cli" and (effective_provider or variant):
            ok, diag = _pi_validate_registration(
                effective_provider,
                registration_model,
                env,
                timeout_s=min(timeout_s, 15),
                isolated=pi_stage is not None,
            )
            if not ok:
                if pi_gcp_token:
                    diag = diag.replace(pi_gcp_token, "[redacted]")
                results.append((False, f"{label}: {diag}"))
                return results

        t0 = time.monotonic()
        try:
            # One launcher for every adapter: an agent CLI is a process tree, and the
            # launcher owns the group (see run_command_in_process_group).
            proc = run_command_in_process_group(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                cwd=Path(pi_stage.name) if pi_stage else adapter.working_directory(merged_config),
            )
            elapsed = time.monotonic() - t0
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            if pi_gcp_token:
                stdout = stdout.replace(pi_gcp_token, "[redacted]")
                stderr = stderr.replace(pi_gcp_token, "[redacted]")
            # pi-cli reports terminal errors as JSONL events with
            # stopReason="error" while still exiting 0. Without this scan
            # the live check false-greens on missing/invalid API keys, 401s,
            # 429s, etc.
            jsonl_error = _extract_pi_jsonl_error(stdout) if adapter_type == "pi-cli" else None
            if proc.returncode == 0 and jsonl_error is None:
                results.append((True, f"{label}: live check passed ({elapsed:.1f}s)"))
                if assert_model:
                    results.append(
                        _evaluate_model_assertion(
                            adapter_type,
                            stdout,
                            assert_model,
                            label,
                            env=env,
                            cmd=cmd,
                        )
                    )
            elif proc.returncode == 0 and jsonl_error is not None:
                category = _classify_provider_error(jsonl_error)
                results.append(
                    (
                        False,
                        (
                            f"{label}: live check failed [{category}] ({elapsed:.1f}s) — "
                            f"{jsonl_error[:240]}"
                        ),
                    )
                )
            else:
                # Extract first useful line of stderr
                err_line = ""
                for line in stderr.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("{"):
                        err_line = stripped[:120]
                        break
                results.append(
                    (
                        False,
                        f"{label}: live check failed (exit {proc.returncode}, {elapsed:.1f}s) — {err_line}",
                    )
                )
        except subprocess.TimeoutExpired:
            results.append((False, f"{label}: live check timed out after {timeout_s}s"))
    finally:
        prompt_path.unlink(missing_ok=True)
        if pi_stage is not None:
            pi_stage.cleanup()

    return results


def _extract_pi_jsonl_error(stdout: str) -> str | None:
    """Scan pi-cli's JSONL stdout for a terminal error event.

    Returns the `errorMessage` string from the first event whose
    `stopReason` is `"error"` (typically `agent_end`, `turn_end`, or
    `message_end`), or None when no such event is present. pi-cli emits
    these on missing/invalid API keys, upstream 4xx/5xx, network failures,
    and quota exhaustion while still exiting 0; without this scanner the
    live check would report PASS for those.
    """

    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError, ValueError:
            continue
        if not isinstance(event, dict):
            continue
        # stopReason can sit on:
        #   - the event itself (turn_end, message_end)
        #   - event.message (some message_end variants)
        #   - event.messages[*] (agent_end carries assembled messages, with
        #     the terminal stopReason on the assistant message)
        candidates: list[object] = [event]
        message = event.get("message")
        if isinstance(message, dict):
            candidates.append(message)
        messages = event.get("messages")
        if isinstance(messages, list):
            candidates.extend(m for m in messages if isinstance(m, dict))
        for node in candidates:
            if not isinstance(node, dict):
                continue
            if node.get("stopReason") != "error":
                continue
            err = node.get("errorMessage")
            if isinstance(err, str) and err:
                return err
            return f"<no errorMessage; event type={event.get('type')!r}>"
    return None


def _classify_provider_error(message: str) -> str:
    """Bucket a provider error message for at-a-glance auth-check output.

    Keeps categories deliberately coarse — exact classification per
    upstream is impossible to keep current. The bucketing exists so an
    operator can tell `auth misconfigured` from `quota exhausted` without
    reading the full upstream payload.
    """
    text = message.lower()
    if any(
        marker in text
        for marker in (
            "failed to resolve api key",
            "api key not found",
            "no api key",
            "missing api key",
        )
    ):
        return "credentials-missing"
    if "401" in text or "unauthorized" in text or "credentials_missing" in text:
        return "unauthorized"
    if "403" in text or "forbidden" in text or "permission_denied" in text:
        return "forbidden"
    if "429" in text or "rate limit" in text or "rate-limit" in text:
        return "rate-limited"
    if any(marker in text for marker in ("quota", "out of usage", "out of credit", "billing")):
        return "quota-exhausted"
    if "404" in text or "not found" in text:
        return "model-not-found"
    if any(marker in text for marker in ("timeout", "timed out", "etimedout")):
        return "timeout"
    if any(marker in text for marker in ("network", "econnrefused", "enotfound")):
        return "network"
    if "500" in text or "internal server" in text or "503" in text:
        return "upstream-5xx"
    return "error"


def _evaluate_model_assertion(
    adapter_type: str,
    stdout: str,
    expected: str,
    label: str,
    *,
    env: Mapping[str, str] | None = None,
    cmd: list[str] | None = None,
) -> tuple[bool, str]:
    """Compare the model the probe's output names against *expected*.

    For an adapter in `_SERVED_MODEL_READERS` this reads the terminal event's
    served-model accounting, so a CLI that rewrites the request to another model
    fails the assertion. codex-cli reports no model at all, so its assertion reads
    the requested model (`_evaluate_codex_model_assertion`); *env* and *cmd* are
    the probe's child environment and argv it needs. Every other adapter's
    assertion reads the model named by its identity event in `_MODEL_EVENT_MATCHERS`.
    """
    if adapter_type in _SERVED_MODEL_READERS:
        return _evaluate_served_model_assertion(adapter_type, stdout, expected, label)
    if adapter_type == "codex-cli":
        return _evaluate_codex_model_assertion(stdout, expected, label, env=env, cmd=cmd)

    observed = _extract_observed_model(adapter_type, stdout)
    if observed is None:
        return (
            False,
            f"{label}: no model event found in stdout (expected model containing {expected!r})",
        )
    if expected in observed:
        return (
            True,
            f"{label}: observed model {observed!r} matches expected {expected!r}",
        )
    return (
        False,
        f"{label}: observed model {observed!r} does not match expected {expected!r}",
    )


def _codex_requested_model(stdout: str, env: Mapping[str, str] | None) -> tuple[str, str] | None:
    """Return Codex's requested model and where it was read, or None when Codex recorded none.

    1. An untyped config preamble, which codex-cli 0.124.0 printed and the pinned
       0.166.0 does not.
    2. The `turn_context` record of the thread's session rollout (plain `.jsonl`
       or compressed `.jsonl.zst`) under `$CODEX_HOME/sessions`: the model Codex
       sent the request for after its own config resolution.

    The probe's own `-m` argument is not evidence: it is what Metaproc asked for,
    so comparing against it could never fail.
    """
    preamble = _extract_observed_model("codex-cli", stdout)
    if preamble is not None:
        return preamble, "the codex-cli config preamble"
    thread_id = codex_thread_id(stdout)
    if thread_id is not None:
        codex_home = resolve_codex_home(env)
        recorded = codex_rollout_model(codex_home, thread_id)
        if recorded is not None:
            return recorded, f"Codex's session rollout for thread {thread_id}"
    return None


def _argv_model(cmd: list[str] | None) -> str | None:
    """Return the `-m` value in the probe's argv, for the failure message only."""
    if cmd and "-m" in cmd:
        index = cmd.index("-m")
        if index + 1 < len(cmd):
            return cmd[index + 1]
    return None


def _evaluate_codex_model_assertion(
    stdout: str,
    expected: str,
    label: str,
    *,
    env: Mapping[str, str] | None,
    cmd: list[str] | None,
) -> tuple[bool, str]:
    """Assert *expected* against the model Codex *requested*, labelled as such.

    `codex exec --json` (0.166.0) names no model in its events, and its rollout
    records only the requested model, so no evidence shows which model served the
    call. The assertion therefore confirms the request Codex made, not the answer,
    and every message says so. Without a rollout or preamble naming the model it
    fails as not verifiable; the probe's `-m` is quoted but never matched.
    """
    found = _codex_requested_model(stdout, env)
    unserved = "codex-cli reports no served model"
    if found is None:
        argv_model = _argv_model(cmd)
        asked = f"; the probe passed -m {argv_model!r}" if argv_model else ""
        missing = (
            f"{label}: cannot verify a model containing {expected!r}: Codex recorded no "
            f"requested model (no config preamble, no session rollout for the thread)"
            f"{asked}, which is what Metaproc asked for, not evidence ({unserved})"
        )
        return (False, missing)
    requested, source = found
    verdict = "matches" if expected in requested else "does not match"
    message = (
        f"{label}: requested model {requested!r} {verdict} expected {expected!r} "
        f"(from {source}; {unserved})"
    )
    return (expected in requested, message)


def _evaluate_served_model_assertion(
    adapter_type: str,
    stdout: str,
    expected: str,
    label: str,
) -> tuple[bool, str]:
    """Assert *expected* against the models the terminal event says billed the call.

    The match is exact: *expected* must be a model that billed, or a moving alias
    (`_SERVED_MODEL_ALIASES`) one of whose served models billed. Any one billing
    model matching passes, because utility and sub-agent requests legitimately bill
    other models beside the requested one. Both the pass and the fail line list
    every billing model with its token count, so a fallback that answered beside or
    instead of the requested model is visible either way.
    """
    served = _extract_served_models(adapter_type, stdout)
    requested = _extract_observed_model(adapter_type, stdout)
    asked = f" (requested {requested!r})" if requested else ""

    if served is None:
        missing = (
            f"{label}: no terminal result event in stdout, so the served model is "
            f"unknown{asked}; cannot confirm model {expected!r}"
        )
        return (False, missing)
    if not served:
        unaccounted = (
            f"{label}: terminal result event reported no served model (no model billed "
            f"tokens){asked}; cannot confirm model {expected!r}"
        )
        return (False, unaccounted)

    billed = format_served_models(served)
    alias_targets = _SERVED_MODEL_ALIASES.get(adapter_type, {}).get(expected)
    accepted = {expected} | (alias_targets or frozenset())
    matching = sorted(
        (name for name in served if name in accepted),
        key=lambda name: (-served[name], name),
    )
    as_alias = ""
    if alias_targets is not None:
        as_alias = f" (alias served as {' or '.join(sorted(alias_targets))})"
    if matching:
        passed = (
            f"{label}: served model {matching[0]!r} matches expected {expected!r}"
            f"{as_alias}; billed: {billed}"
        )
        return (True, passed)
    mismatch = (
        f"{label}: no served model matches expected {expected!r}{as_alias}{asked} — the "
        f"CLI answered with a different model; billed: {billed}"
    )
    return (False, mismatch)


def _claude_local_live_probe_passed(results: list[tuple[bool, str]]) -> bool:
    """True when Phase 2 proved local claude-code-cli can reach the model.

    The Secret Manager probe is a remote-worker credential diagnostic. For
    local runs it is useful visibility, but it should not turn a verified local
    Claude environment red just because the remote secret is slow or stale.
    """
    return any(
        ok and msg.startswith("claude-code-cli") and "live check passed" in msg
        for ok, msg in results
    )


def _run_claude_secret_live_check(
    secret_ref: str,
    timeout_s: int,
) -> list[tuple[bool, str]]:
    """Validate a Secret Manager claude-code credential end-to-end.

    Exercises the cloud path that GCP Batch workers use: fetch the Secret
    Manager payload, materialize it in a temporary credential slot, and run
    ``claude -p`` with that slot's credential and configuration paths.

    ``secret_ref`` must be a fully-qualified resource name:
    ``projects/PROJECT/secrets/NAME/versions/VERSION``.
    """

    label = f"claude-code-cli [secret={secret_ref.split('/secrets/')[-1]}]"
    results: list[tuple[bool, str]] = []

    try:
        proc = subprocess.run(
            ["gcloud", "secrets", "versions", "access", secret_ref],
            capture_output=True,
            text=True,
            timeout=min(timeout_s, 15),
        )
    except subprocess.TimeoutExpired:
        return [(False, f"{label}: gcloud access timed out after {min(timeout_s, 15)}s")]
    except FileNotFoundError:
        return [(False, f"{label}: gcloud not found on PATH — install Google Cloud SDK")]

    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        first_err = next((line for line in err if line.strip()), "gcloud failed")[:160]
        return [(False, f"{label}: fetch failed — {first_err}")]

    adapter = get_auth_capable("claude-code-cli")
    if adapter is None or not adapter.check_auth().cli_found:
        return [(False, f"{label}: claude binary not found on PATH")]

    merged_config: dict[str, object] = {
        "permission_mode": "default",
        "output_format": "text",
        "verbose": False,
        "no_session_persistence": True,
        "strict_mcp_config": True,
        "tools": [],
    }

    with tempfile.TemporaryDirectory(prefix="metaproc-claude-secret-check-") as tmp:
        tmp_home = Path(tmp)
        claude_dir = tmp_home / ".claude"
        try:
            adapter.materialize_credential(claude_dir, proc.stdout)
        except RuntimeError as exc:
            return [(False, f"{label}: payload rejected — {exc}")]

        prompt_path = tmp_home / "prompt.md"
        atomic_write_text(prompt_path, "Respond with exactly: OK")
        cmd = adapter.build_command(
            prompt_file=prompt_path, merged_config=merged_config, variables={}
        )
        cmd = [cmd[0], "--setting-sources", "user", *cmd[1:]]
        env = apply_slot_env(
            adapter.prepare_env(_isolated_probe_env(agent_seed_env()), merged_config),
            scope_env=adapter.credential_scope_env(claude_dir),
            scrub_env=adapter.credential_scrub_env(),
            extra={"HOME": str(tmp_home), "XDG_CONFIG_HOME": str(tmp_home / ".config")},
        )

        t0 = time.monotonic()
        try:
            run = run_command_in_process_group(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                cwd=tmp_home,
            )
            elapsed = time.monotonic() - t0
            if run.returncode == 0:
                results.append((True, f"{label}: live check passed ({elapsed:.1f}s)"))
            else:
                err_line = ""
                for line in (run.stderr or run.stdout or "").splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("{"):
                        err_line = stripped[:160]
                        break
                results.append(
                    (
                        False,
                        f"{label}: live check failed (exit {run.returncode}, {elapsed:.1f}s) — {err_line}",
                    )
                )
        except subprocess.TimeoutExpired:
            results.append((False, f"{label}: live check timed out after {timeout_s}s"))

    return results


def _run_readiness_check(run_dir: Path) -> list[tuple[bool, str]]:
    """Check that a run directory is ready for execution."""
    results: list[tuple[bool, str]] = []

    # Check run dir exists
    if not run_dir.is_dir():
        results.append((False, f"run dir: {run_dir} does not exist"))
        return results
    results.append((True, f"run dir: {run_dir} exists"))

    # Look for progress.md in the run dir or one level down
    progress_candidates = [
        run_dir / "progress.md",
        *run_dir.glob("*/progress.md"),
    ]
    progress_file = next((p for p in progress_candidates if p.exists()), None)

    if progress_file:
        # Count items by reading frontmatter
        try:
            fm = fmf_read_frontmatter(progress_file)
            progress = fm.get("progress", fm) if fm else {}
            items = progress.get("items", []) if isinstance(progress, dict) else []
            total = len(items) if isinstance(items, list) else 0
            results.append((True, f"progress: {progress_file.name} — {total} items"))
        except Exception as exc:
            results.append((False, f"progress: failed to parse {progress_file} — {exc}"))
    else:
        results.append((False, "progress: no progress.md found"))

    # Check output dir is writable. The probe name must be unique: a fixed one lets
    # two concurrent checks unlink each other's file, and the loser then reports a
    # perfectly writable directory as unwritable. Clean up even when the write fails.
    try:
        with temp_output_file(prefix=".auth-check-write-test-", dir=run_dir, always_clean=True) as (
            fd,
            _probe,
        ):
            _ = os.write(fd, b"test")
        results.append((True, "output dir: writable"))
    except OSError as exc:
        results.append((False, f"output dir: not writable — {exc}"))

    # Disk space
    ok, msg = check_disk_space(path=str(run_dir))
    results.append((ok, msg))

    return results


@app.command("auth-check")
def auth_check(
    live: bool = typer.Option(False, "--live", help="Send a test prompt to each model (Phase 2)."),
    run_dir: Path | None = typer.Option(  # noqa: UP007
        None, "--run-dir", help="Check run directory readiness (Phase 3)."
    ),
    variant: str | None = typer.Option(  # noqa: UP007
        None, "--variant", help="Test specific variant only."
    ),
    variant_file: list[Path] = typer.Option(
        [],
        "--variant-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    profile_file: list[Path] = typer.Option(
        [],
        "--profile-file",
        help="Additional execution-profile registry file (repeatable).",
    ),
    provider: str | None = typer.Option(  # noqa: UP007
        None,
        "--provider",
        help=(
            "Explicit pi-cli provider override for --live. When unset, the provider "
            "is inferred from the variant name (e.g. *-maas → vertex-maas)."
        ),
    ),
    timeout: int = typer.Option(30, "--timeout", help="Per-check timeout in seconds for --live."),
    claude_secret_ref: str | None = typer.Option(  # noqa: UP007
        None,
        "--claude-secret-ref",
        help=(
            "Validate a claude-code-cli OAuth credential stored in GCP Secret Manager "
            "by materializing it into a throwaway home dir and pinging the API. "
            "Accepts a resource name (projects/P/secrets/N/versions/V). "
            "Defaults to $METAPROC_GCP_SECRET_CLAUDE_CREDS when set."
        ),
    ),
    assert_model: str | None = typer.Option(  # noqa: UP007
        None,
        "--assert-model",
        help=(
            "Fail the --live probe unless the model its output names contains this "
            "substring. Where that model is read differs by adapter. "
            "gemini-cli: the terminal result event's stats.models, the models that "
            "billed the call, matched exactly (an alias such as auto matches the models "
            "it is served as), so a request the CLI rewrote or answered with a "
            "fallback fails. "
            "claude-code-cli: system.init.model, the model Claude Code resolved the "
            "request to; its result.modelUsage carries the models that billed but is "
            "not read yet. "
            "pi-cli: message.model on the first assistant message_start event. "
            "codex-cli: the model Codex requested, read from the thread's session rollout; "
            "with no rollout the assertion fails as not verifiable. codex exec reports no "
            "model that served the call."
        ),
    ),
    adapter: str | None = typer.Option(  # noqa: UP007
        None,
        "--adapter",
        help=(
            "Adapter name (gemini-cli, pi-cli, claude-code-cli, codex-cli) for "
            "ad-hoc probing without an execution profile. Pair with --model to "
            "exercise an arbitrary (adapter, model) combination — e.g. a Gemini "
            "model that isn't in execution-profiles.default.yaml. Mutually "
            "exclusive with --variant."
        ),
    ),
    model: str | None = typer.Option(  # noqa: UP007
        None,
        "--model",
        help=(
            "Model ID to dispatch through --adapter. Use to smoke any model "
            "without first adding it as an execution profile. Requires --adapter."
        ),
    ),
) -> None:
    """Verify adapters, credentials, and optionally live API connectivity."""
    out = get_output()
    all_results: list[tuple[bool, str]] = []
    live_results: list[tuple[bool, str]] = []
    any_failed = False

    # ── Phase 1: Environment and credential audit ──
    out.progress("Phase 1: Environment and credential audit")

    # Disk space
    environment_results: list[tuple[bool | None, str]] = [check_disk_space()]

    # gcloud token (informational — needed for vertex-maas adapters)
    gcloud_ok, gcloud_msg = _check_gcloud_token()
    environment_results.append((gcloud_ok, gcloud_msg))

    # --adapter is mutually exclusive with --variant. When --adapter is set we
    # bypass execution-profile resolution and probe (adapter, model) directly;
    # this is the ad-hoc path used by smoke-gemini-matrix to exercise every
    # registered Gemini model without minting one execution profile per cell.
    if adapter and variant:
        raise CLIError("--adapter and --variant are mutually exclusive")
    if model and not adapter:
        raise CLIError("--model requires --adapter")
    if adapter and adapter not in ADAPTER_REGISTRY:
        raise CLIError(f"unknown adapter {adapter!r} (registered: {sorted(ADAPTER_REGISTRY)})")

    # Adapter checks. When a variant or --adapter is supplied, scope Phase 1
    # to the relevant adapter instead of failing on unrelated CLIs.
    scoped_adapter, scoped_model, scoped_provider, scoped_config = _resolve_variant_target(
        variant,
        profile_files=[*variant_file, *profile_file],
    )
    if adapter:
        scoped_adapter, scoped_model = adapter, model
    adapter_types = [scoped_adapter] if scoped_adapter else list(ADAPTER_REGISTRY)
    for adapter_type in adapter_types:
        environment_results.extend(_check_adapter(adapter_type, live=live))

    # Print Phase 1 results
    for ok, msg in environment_results:
        if ok is None:
            out.data(f"[!] {msg}")
            continue
        all_results.append((ok, msg))
        icon = "+" if ok else "-"
        out.data(f"[{icon}] {msg}")
        if not ok:
            any_failed = True

    # ── Phase 2: Live LLM provider health ──
    if live:
        out.progress("\nPhase 2: Live LLM provider health")

        if variant or adapter:
            live_results.extend(
                _run_live_check(
                    scoped_adapter or "pi-cli",
                    scoped_model,
                    timeout,
                    provider=provider or scoped_provider,
                    assert_model=assert_model,
                    profile_config=scoped_config,
                )
            )
        else:
            for adapter_type in ADAPTER_REGISTRY:
                live_results.extend(
                    _run_live_check(
                        adapter_type,
                        None,
                        timeout,
                        provider=provider,
                        assert_model=assert_model,
                    )
                )

        for ok, msg in live_results:
            icon = "+" if ok else "-"
            out.data(f"[{icon}] {msg}")
            if not ok:
                any_failed = True
        all_results.extend(live_results)

    # ── Phase 2b: claude-code-cli Secret Manager credential ──
    # Phase 2b is claude-specific. Skip it when the operator scoped the
    # check to a different adapter so per-adapter smokes don't inherit a
    # red signal that belongs to another provider.
    effective_secret_ref = (
        claude_secret_ref or MetaprocEnv.METAPROC_GCP_SECRET_CLAUDE_CREDS.read_str(default=None)
    )
    phase2b_applies = scoped_adapter in (None, "claude-code-cli")
    if effective_secret_ref and phase2b_applies:
        out.progress("\nPhase 2b: claude-code-cli Secret Manager credential")
        secret_results = _run_claude_secret_live_check(effective_secret_ref, timeout)
        secret_required = claude_secret_ref is not None or not _claude_local_live_probe_passed(
            live_results
        )
        for ok, msg in secret_results:
            advisory = not ok and not secret_required
            if advisory:
                msg = (
                    f"{msg} (advisory; local claude-code-cli live check passed, "
                    "not blocking this local run; pass --claude-secret-ref to require it)"
                )
            effective_ok = ok or advisory
            icon = "!" if advisory else ("+" if effective_ok else "-")
            out.data(f"[{icon}] {msg}")
            if not effective_ok:
                any_failed = True
            all_results.append((effective_ok, msg))

    # ── Phase 3: Run readiness ──
    if run_dir:
        out.progress("\nPhase 3: Run readiness")
        readiness_results = _run_readiness_check(run_dir)
        for ok, msg in readiness_results:
            icon = "+" if ok else "-"
            out.data(f"[{icon}] {msg}")
            if not ok:
                any_failed = True
        all_results.extend(readiness_results)

    # Summary
    passed = sum(1 for ok, _ in all_results if ok)
    failed = sum(1 for ok, _ in all_results if not ok)
    out.progress(f"\n{'PASS' if not any_failed else 'FAIL'}: {passed} passed, {failed} failed")

    if any_failed:
        raise typer.Exit(code=1)
