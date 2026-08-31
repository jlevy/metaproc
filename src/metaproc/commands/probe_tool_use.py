"""metaproc probe-tool-use — round-trip a single tool call through a model.

Writes a tempfile with a unique sentinel string, asks the model to read it
through its native file-read tool, then verifies the sentinel appears in
the model's response. The sentinel is high-entropy (16 hex chars) so the
model cannot hallucinate it: presence in the response proves a tool call
executed and the result was re-fed.

This catches the class of bug where a model text-generates fine but cannot
complete a tool round-trip — most notably the Gemini-3 ``thought_signature``
regression on the pi-cli openai-completions path (see
``docs/runbooks/adapter-compatibility.runbook.md`` §1).
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from metaproc.adapters.base import agent_seed_env
from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cli import app, get_output
from metaproc.config.providers import infer_provider
from metaproc.errors import CLIError
from metaproc.models.execution_profile import ExecutionProfileRegistry

log = logging.getLogger(__name__)


# Keys whose string values carry assistant-emitted text in either harness's
# stream JSON (gemini-cli stream-json `content`/`delta`; pi-cli json mode
# `delta`/`text`/`content`/`output`). Excludes identifier-style keys like
# `model`, `tool_name`, `id` so timestamps and metadata can't accidentally
# satisfy a sentinel check.
_TEXT_KEYS: frozenset[str] = frozenset({"content", "delta", "text", "output"})


def _walk_text_strings(node: object, parent_key: str | None = None) -> list[str]:
    """Collect every string whose containing JSON key is in _TEXT_KEYS."""
    out: list[str] = []
    if isinstance(node, str):
        if parent_key in _TEXT_KEYS:
            out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_text_strings(v, parent_key=k))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk_text_strings(v, parent_key=parent_key))
    return out


def _sentinel_present(stdout: str, sentinel: str) -> bool:
    """Return True if *sentinel* appears in the harness's stdout.

    Plain substring first (fast path). On miss, parse each JSON line, collect
    every string whose containing key is a known text-bearing key
    (content/delta/text/output), concatenate them in stream order, and search
    again. This handles stream-json chunking that splits tokens across
    consecutive delta events (gemini-cli often splits the response into
    multiple `content` chunks).
    """
    if sentinel in stdout:
        return True
    accumulator: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not (line.startswith(("{", "["))):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        accumulator.extend(_walk_text_strings(event))
    return sentinel in "".join(accumulator)


def _cleanup_probe_dir(probe_dir: Path) -> None:
    """Best-effort cleanup of the per-probe scratch dir."""
    try:
        shutil.rmtree(probe_dir, ignore_errors=True)
        parent = probe_dir.parent
        if parent.name == ".metaproc-probe" and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _build_probe_config(harness: str, model: str, provider: str | None) -> dict[str, object]:
    """Build the minimum merged_config for a tool-use probe per harness."""
    cfg: dict[str, object] = {"model": model}
    if harness == "gemini-cli":
        cfg["permission_mode"] = "bypassPermissions"  # → --approval-mode yolo
        cfg["output_format"] = "stream-json"
    elif harness == "pi-cli":
        eff_provider = provider
        if not eff_provider:
            spec = infer_provider(model)
            eff_provider = spec.name if spec else None
        if eff_provider:
            cfg["provider"] = eff_provider
        cfg["no_session_persistence"] = True
    elif harness == "claude-code-cli":
        cfg["permission_mode"] = "bypassPermissions"
        cfg["output_format"] = "stream-json"
        cfg["verbose"] = True
        cfg["no_session_persistence"] = True
    elif harness == "codex-cli":
        cfg["permission_mode"] = "bypassPermissions"
    return cfg


def _run_probe(
    harness: str,
    model: str,
    provider: str | None,
    timeout_s: int,
) -> tuple[bool, str, str]:
    """Execute one probe round-trip. Returns (ok, summary, stdout)."""
    if harness not in ADAPTER_REGISTRY:
        return (
            False,
            f"unknown harness {harness!r} (registered: {sorted(ADAPTER_REGISTRY)})",
            "",
        )

    sentinel = f"PROBE-SENTINEL-{secrets.token_hex(8).upper()}"
    # Probe file must live inside the current working directory: gemini-cli's
    # `read_file` tool enforces a workspace gate that rejects absolute paths
    # outside the launch cwd, and pi-cli's `read` tool resolves relative
    # paths against cwd. Tempfiles in /tmp would fail both. The dir name is
    # randomized so concurrent probes don't collide.
    probe_dir = Path.cwd() / ".metaproc-probe" / secrets.token_hex(4)
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "probe-target.txt"
    probe_file.write_text(f"{sentinel}\nLine 2\nLine 3\n")

    rel_path = probe_file.relative_to(Path.cwd())
    # Phrase the prompt as a direct user question (not an imperative instruction
    # block) so claude-code-cli's @file prompt-injection heuristic does not
    # flag it. Claude reads prompts passed via `-p @<path>` from disk and
    # treats imperatives that look like "use your tool to do X" as suspicious
    # injection. A declarative "what is the first line of file X?" reads as
    # a legitimate user question. Gemini and pi-cli are unaffected by the
    # wording change.
    prompt_text = (
        f"What is the first line of the file {rel_path}? "
        f"Read it and reply with that line only, exactly as it appears.\n"
    )
    prompt_file = probe_dir / "probe-prompt.md"
    prompt_file.write_text(prompt_text)

    adapter = ADAPTER_REGISTRY[harness]
    merged_config = _build_probe_config(harness, model, provider)

    try:
        cmd = adapter.build_command(
            prompt_file=prompt_file,
            merged_config=merged_config,
            variables={},
        )
        env = adapter.prepare_env(agent_seed_env(), merged_config)
    except Exception as exc:  # noqa: BLE001
        return False, f"build_command/prepare_env failed: {exc}", ""

    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _cleanup_probe_dir(probe_dir)
        return False, f"timed out after {timeout_s}s", ""

    stdout = proc.stdout or ""
    _cleanup_probe_dir(probe_dir)
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        tail = stderr[-500:] if stderr else "(no stderr)"
        return (
            False,
            f"exit code {proc.returncode}; stderr tail: {tail!r}",
            stdout,
        )

    if not _sentinel_present(stdout, sentinel):
        return (
            False,
            f"sentinel {sentinel!r} not found in stdout — tool round-trip failed",
            stdout,
        )

    return True, f"sentinel {sentinel!r} echoed via tool round-trip", stdout


@app.command("probe-tool-use")
def probe_tool_use(
    harness: str | None = typer.Option(  # noqa: UP007
        None,
        "--harness",
        help=("Adapter name. One of: gemini-cli, pi-cli, claude-code-cli, codex-cli."),
    ),
    model: str | None = typer.Option(  # noqa: UP007
        None,
        "--model",
        help="Model ID to dispatch through the harness (e.g. gemini-3.5-flash).",
    ),
    provider: str | None = typer.Option(  # noqa: UP007
        None,
        "--provider",
        help=(
            "Provider override for pi-cli. If omitted, inferred from the model "
            "ID via metaproc.config.providers.infer_provider."
        ),
    ),
    execution_profile: str | None = typer.Option(  # noqa: UP007
        None,
        "--execution-profile",
        help=(
            "Use this execution profile to derive harness, model, and provider. "
            "Mutually exclusive with --harness. Exercises the same profile "
            "resolution path large workflow dispatch uses, so a green probe here proves "
            "the profile is dispatch-ready end-to-end."
        ),
    ),
    timeout: int = typer.Option(
        60,
        "--timeout",
        help="Subprocess timeout in seconds.",
    ),
    show_stdout_on_fail: bool = typer.Option(
        True,
        "--show-stdout-on-fail/--no-show-stdout-on-fail",
        help="Print the first 2KB of subprocess stdout when the probe fails.",
    ),
) -> None:
    """Round-trip a file-read tool call through a model and verify the result.

    A unique sentinel string is written to a tempfile; the model is asked
    to read it and echo the first line. Exits 0 if the sentinel appears in
    the model's stdout (proving a tool call executed and its result was
    re-fed); exits non-zero otherwise. Use to catch models that text-generate
    fine but cannot complete tool calls (e.g. Gemini-3 thought_signature
    regressions).
    """
    if execution_profile and (harness or model or provider):
        raise CLIError(
            "--execution-profile is mutually exclusive with --harness/--model/--provider"
        )
    if execution_profile:
        registry = ExecutionProfileRegistry.load()
        resolved = registry.resolve(execution_profile)
        profile = resolved.profile
        harness = profile.adapter
        model = str(profile.config.get("model") or profile.model or "")
        provider = str(profile.config.get("provider") or profile.provider or "") or None
        if not model:
            raise CLIError(f"execution profile {execution_profile!r} does not declare a model")
    if not harness or not model:
        raise CLIError("must specify either --execution-profile, or both --harness and --model")

    out = get_output()
    label = f"{harness} ({model})"
    if execution_profile:
        label = f"profile={execution_profile} → {label}"
    out.progress(f"probe-tool-use: {label} (timeout={timeout}s)")

    ok, summary, stdout = _run_probe(harness, model, provider, timeout)

    if ok:
        out.progress(f"{label}: PASS — {summary}")
        return

    sys.stderr.write(f"{label}: FAIL — {summary}\n")
    if show_stdout_on_fail and stdout:
        sys.stderr.write("--- subprocess stdout (first 2KB) ---\n")
        sys.stderr.write(stdout[:2000])
        if not stdout.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.write("--- end stdout ---\n")
    raise CLIError(f"tool-use probe failed: {summary}")
