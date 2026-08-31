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
import subprocess
import tempfile
import time
from pathlib import Path

import typer

from metaproc.adapters.base import agent_seed_env
from metaproc.adapters.pi_cli import resolve_pi_binary
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app, get_output
from metaproc.commands.claude_auth import _validate_creds_payload
from metaproc.config.env_vars import MetaprocEnv
from metaproc.config.providers import infer_provider, provider_by_name
from metaproc.engine.preflight import check_disk_space
from metaproc.errors import CLIError
from metaproc.io import fmf_read_frontmatter
from metaproc.models.execution_profile import ExecutionProfileRegistry

log = logging.getLogger(__name__)


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


def _check_adapter(adapter_type: str) -> list[tuple[bool, str]]:
    """Run credential checks for a single adapter."""
    adapter = ADAPTER_REGISTRY[adapter_type]
    status = adapter.check_auth()
    results: list[tuple[bool, str]] = []

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


def _resolve_variant_target(
    variant: str | None,
    *,
    profile_files: list[Path] | tuple[Path, ...] = (),
) -> tuple[str | None, str | None, str | None]:
    """Resolve a profile or raw adapter name to (adapter_type, model, provider)."""
    if not variant:
        return None, None, None

    registry = ExecutionProfileRegistry.load(
        repo_root=_find_repo_root(Path.cwd()),
        explicit_files=tuple(profile_files),
    )
    if variant in registry.entries:
        resolved = registry.resolve(variant)
        profile = resolved.profile
        return (
            profile.adapter,
            str(profile.config.get("model") or profile.model or "") or None,
            str(profile.config.get("provider") or profile.provider or "") or None,
        )

    if variant in ADAPTER_REGISTRY:
        return variant, None, None

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


def _pi_list_models(env: dict[str, str], timeout_s: int = 15) -> tuple[bool, str]:
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

    try:
        proc = subprocess.run(
            [pi_binary, "--list-models"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
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
) -> tuple[bool, str]:
    """Verify that ``provider`` and ``model`` are registered with pi-cli.

    Runs ``pi --list-models`` under ``env`` and looks for the provider name
    and model ID in the output. This is the gate that stops
    ``auth-check --live`` from false-greening when pi-cli silently falls
    back to its default provider.
    """
    ok, output = _pi_list_models(env, timeout_s=timeout_s)
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


# Adapter-specific matchers for the identity event that carries model ID.
# Each entry is (event type or None, event subtype or None, dotted-path to
# model within the event). `None` for type means "any line" — used for
# codex-cli, whose config-dump preamble carries `{"model": ..., ...}` with
# no `type` field. The first JSONL line matching both type/subtype (when
# specified) and containing a non-empty string at *model_path* wins.
_MODEL_EVENT_MATCHERS: dict[str, tuple[str | None, str | None, tuple[str, ...]]] = {
    "claude-code-cli": ("system", "init", ("model",)),
    "gemini-cli": ("init", None, ("model",)),
    # pi-cli emits `agent_start` without model; the model ID lives under
    # `message.model` on the first `message_start` event emitted by the
    # assistant turn. Matching `message_start` directly gives us the
    # identity event even when agent_start is empty.
    "pi-cli": ("message_start", None, ("message", "model")),
    # codex-cli 0.124.0 prints an untyped config-dump line as its first
    # stdout line (e.g. `{"model":"gpt-5","provider":"openai",...}`). The
    # typed event stream (thread.started → turn.started → item.* →
    # turn.completed) does NOT carry a model ID, so this preamble is the
    # only place the observed model shows up.
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
        except (json.JSONDecodeError, ValueError):
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


def _run_live_check(
    adapter_type: str,
    variant: str | None,
    timeout_s: int,
    provider: str | None = None,
    *,
    assert_model: str | None = None,
) -> list[tuple[bool, str]]:
    """Send a trivial prompt to an adapter and report success + latency.

    When *assert_model* is set, also parse the subprocess stdout for the
    adapter's identity event and verify the observed model contains the
    expected substring. codex-cli does not emit a model ID in its JSONL
    stream; for that adapter, an informational line is emitted instead.
    """

    adapter = ADAPTER_REGISTRY[adapter_type]
    status = adapter.check_auth()
    if not status.cli_found or not status.credentials_found:
        return [(False, f"{adapter_type}: skipping live check (no binary or credentials)")]

    # Build a minimal config — just enough for a trivial prompt.
    # For pi-cli, pin the provider explicitly so the live check exercises the
    # requested provider rather than falling back to pi-cli's default — that
    # fallback is what produced the vertex-maas false-green (see Phase 0c
    # blocker report).
    merged_config: dict[str, object] = {}
    if variant:
        merged_config["model"] = variant
    effective_provider = provider
    if adapter_type == "pi-cli" and variant and not effective_provider:
        effective_provider = _infer_pi_provider(variant)
    if adapter_type == "pi-cli" and effective_provider:
        merged_config["provider"] = effective_provider
    if adapter_type == "claude-code-cli":
        merged_config.setdefault("permission_mode", "bypassPermissions")
        # assert_model needs the `system.init` event, which only appears
        # under stream-json. Plain text output carries no model marker.
        default_format = "stream-json" if assert_model else "text"
        merged_config.setdefault("output_format", default_format)
        merged_config["verbose"] = merged_config["output_format"] == "stream-json"
        merged_config["no_session_persistence"] = True
    if adapter_type == "gemini-cli" and assert_model:
        # Same reasoning: need the `init` event carrying `model`.
        merged_config.setdefault("output_format", "stream-json")
    if adapter_type == "codex-cli":
        # Codex refuses to dispatch without a permission signal because it
        # would otherwise prompt for approval on every tool call and
        # deadlock in batch mode (stdin=DEVNULL). A trivial "Respond with
        # exactly: OK" prompt never executes tools, so bypassing approvals
        # is the minimum safe default for the live probe.
        merged_config.setdefault("permission_mode", "bypassPermissions")

    results: list[tuple[bool, str]] = []
    label = f"{adapter_type}" + (f" ({variant})" if variant else "")

    # Write a trivial prompt to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("Respond with exactly: OK")
        prompt_path = Path(f.name)

    try:
        cmd = adapter.build_command(
            prompt_file=prompt_path,
            merged_config=merged_config,
            variables={},
        )

        # Disable function-tools for pi-cli live smokes so the dispatch
        # exercises a clean roundtrip without hitting OpenAI's
        # `/v1/chat/completions` constraint that gpt-5.x reasoning models
        # reject `reasoning_effort + function tools` (operators must use
        # `/v1/responses` for that combo). The trivial "Respond with OK"
        # prompt does not need tools, and turning them off keeps the
        # smoke compatible across providers.
        if adapter_type == "pi-cli" and "--no-tools" not in cmd:
            cmd = [*cmd, "--no-tools"]

        # Inject a fresh GCP access token as `--api-key` only for providers
        # that actually use one (e.g. vertex-maas). Doing this unconditionally
        # for pi-cli false-greens direct-API providers: the GCP token gets
        # passed in place of (e.g.) DEEPSEEK_API_KEY, then pi-cli reports the
        # real auth failure as a JSONL `stopReason: error` event while still
        # exiting 0 — caught below by the JSONL error scanner.
        if adapter_type == "pi-cli" and effective_provider:
            spec = provider_by_name(effective_provider)
            if spec is not None and spec.requires_gcp_token:
                try:
                    from metaproc.cloud.gcp.resolve_token import (  # noqa: PLC0415 -- optional [gcp-batch] dependency
                        resolve_gcp_token,  # noqa: PLC0415  # optional GCP extra
                    )

                    token = resolve_gcp_token()
                    cmd = [cmd[0], "--api-key", token, *cmd[1:]]
                except ImportError:
                    pass  # GCP extra not installed — skip token injection
                except Exception:
                    log.warning("Failed to inject GCP token for live check", exc_info=True)

        env = adapter.prepare_env(agent_seed_env(), merged_config)

        # Pre-flight: confirm pi-cli actually knows the requested provider/model
        # before we dispatch a trivial prompt. Without this gate, pi-cli
        # silently falls back to its default provider and the live check
        # reports PASS against a completely different model.
        if adapter_type == "pi-cli" and (effective_provider or variant):
            ok, diag = _pi_validate_registration(
                effective_provider,
                variant,
                env,
                timeout_s=min(timeout_s, 15),
            )
            if not ok:
                results.append((False, f"{label}: {diag}"))
                return results

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
            )
            elapsed = time.monotonic() - t0
            # pi-cli reports terminal errors as JSONL events with
            # stopReason="error" while still exiting 0. Without this scan
            # the live check false-greens on missing/invalid API keys, 401s,
            # 429s, etc.
            jsonl_error = (
                _extract_pi_jsonl_error(proc.stdout or "") if adapter_type == "pi-cli" else None
            )
            if proc.returncode == 0 and jsonl_error is None:
                results.append((True, f"{label}: live check passed ({elapsed:.1f}s)"))
                if assert_model:
                    results.append(
                        _evaluate_model_assertion(
                            adapter_type, proc.stdout or "", assert_model, label
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
                for line in (proc.stderr or "").splitlines():
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
        except (json.JSONDecodeError, ValueError):
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
) -> tuple[bool, str]:
    """Compare observed model from stdout against *expected* substring."""
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
    Manager payload, materialize it to a throwaway ``~/.claude/.credentials.json``,
    and run ``claude -p`` against it. Catches stale/invalidated OAuth tokens
    that the local keychain check cannot detect because the keychain blob
    and the Secret Manager blob can drift apart.

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

    payload = proc.stdout
    try:
        _validate_creds_payload(payload)
    except RuntimeError as exc:
        return [(False, f"{label}: payload rejected — {exc}")]

    adapter = ADAPTER_REGISTRY["claude-code-cli"]
    status = adapter.check_auth()
    if not status.cli_found:
        return [(False, f"{label}: claude binary not found on PATH")]

    merged_config: dict[str, object] = {
        "permission_mode": "bypassPermissions",
        "output_format": "text",
        "verbose": False,
        "no_session_persistence": True,
    }

    with tempfile.TemporaryDirectory(prefix="metaproc-claude-secret-check-") as tmp:
        tmp_home = Path(tmp)
        claude_dir = tmp_home / ".claude"
        claude_dir.mkdir(mode=0o700)
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(payload)
        creds_file.chmod(0o600)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as prompt_f:
            prompt_f.write("Respond with exactly: OK")
            prompt_path = Path(prompt_f.name)

        try:
            cmd = adapter.build_command(
                prompt_file=prompt_path,
                merged_config=merged_config,
                variables={},
            )
            env = adapter.prepare_env(agent_seed_env(), merged_config)
            env.pop("ANTHROPIC_API_KEY", None)
            env["HOME"] = str(tmp_home)

            t0 = time.monotonic()
            try:
                run = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    env=env,
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
        finally:
            prompt_path.unlink(missing_ok=True)

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

    # Check output dir is writable
    try:
        test_file = run_dir / ".auth-check-write-test"
        test_file.write_text("test")
        test_file.unlink()
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
            "Verify the live --live probe actually dispatched against a model "
            "whose identity contains this substring. Catches silent --model "
            "fallbacks (claude/gemini warn-and-default on unknown names). "
            "codex-cli's event stream does not carry model ID, so for codex "
            "this emits an informational line rather than a hard assertion; "
            "the codex guarantee is covered by a separate negative-control smoke."
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
    all_results.append(check_disk_space())

    # gcloud token (informational — needed for vertex-maas adapters)
    gcloud_ok, gcloud_msg = _check_gcloud_token()
    all_results.append((gcloud_ok, gcloud_msg))

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
    scoped_adapter, scoped_model, scoped_provider = _resolve_variant_target(
        variant,
        profile_files=[*variant_file, *profile_file],
    )
    if adapter:
        scoped_adapter, scoped_model = adapter, model
    adapter_types = [scoped_adapter] if scoped_adapter else list(ADAPTER_REGISTRY)
    for adapter_type in adapter_types:
        all_results.extend(_check_adapter(adapter_type))

    # Print Phase 1 results
    for ok, msg in all_results:
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
