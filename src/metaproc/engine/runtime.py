"""Runtime execution helpers — step preparation, launch, batch management."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from strif import atomic_output_file

from metaproc.adapters.registry import get_adapter
from metaproc.engine.pathing import compute_log_filename, compute_logs_dir, compute_task_logs_dir
from metaproc.engine.placeholders import (
    check_unresolved_placeholders,
    resolve_output_paths,
    resolve_templates,
)
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep
from metaproc.models.runtime import StatusRecord

log = logging.getLogger(__name__)

# Pi RPC/JSON mode events that are cumulative per-token and cause O(n^2) log growth.
# These are dropped at write time by the filter thread; compaction handles the rest.
_PI_LIVE_DROP_EVENTS = frozenset({"message_update", "tool_execution_update"})

DEFAULT_BATCH_SIZE = 10


@dataclass
class LaunchResult:
    """Result of launching a subprocess."""

    proc: subprocess.Popen[bytes]
    pid: int
    log_path: Path
    pid_path: Path
    item: str
    item_dir: Path | None = None
    item_context: dict[str, str] = field(default_factory=dict)
    running_record: StatusRecord | None = None
    started_monotonic: float = 0.0
    timeout_s: int | None = None
    attempt: int = 1
    filter_thread: threading.Thread | None = None


def resolve_batch_size(step_def: ProcessStep, batch_size_override: int | None = None) -> int:
    """Resolve the effective batch size for a fan-out step."""
    if batch_size_override is not None:
        value: int | None = batch_size_override
    else:
        for_each = step_def.for_each
        value = for_each.batch_size if for_each else None
    if value is None:
        return DEFAULT_BATCH_SIZE
    if value <= 0:
        msg = f"batch_size must be > 0, got: {value}"
        raise ValueError(msg)
    return value


def prepare_step(
    spec: ProcessSpec,
    step_def: ProcessStep,
    variables: dict[str, str],
    process_dir: Path,
    output_specs: dict[str, IOSpec] | None = None,
    prompt_paths: list[str] | None = None,
    create_dirs: bool = True,
) -> tuple[str, Path, Path]:
    """Resolve prompt, compute paths for a step.

    Returns (resolved_prompt, logs_dir, log_path).

    When *create_dirs* is False, skip ``mkdir`` for log directories — useful
    when resolving prompts for cloud dispatch where log paths are remote.
    """
    prompt = step_def.prompt_prefix or ""
    resolved_prompt_paths: list[str] = []
    for prompt_path in prompt_paths if prompt_paths is not None else step_def.prompt_paths:
        resolved_prompt_path = Path(resolve_templates(prompt_path, variables))
        if not resolved_prompt_path.is_absolute():
            resolved_prompt_path = (process_dir / resolved_prompt_path).resolve()
        if not resolved_prompt_path.is_file():
            raise FileNotFoundError(
                f"step '{step_def.id}': prompt file not found: {resolved_prompt_path}"
            )
        resolved_prompt_paths.append(str(resolved_prompt_path))

    if resolved_prompt_paths:
        variables["step.prompt_paths"] = ", ".join(resolved_prompt_paths)
        variables["step.prompt_path"] = resolved_prompt_paths[0]
        for resolved_prompt_path in resolved_prompt_paths:
            prompt_content = Path(resolved_prompt_path).read_text()
            prompt = (
                f"{prompt.rstrip()}\n\n"
                f'<prompt-file path="{resolved_prompt_path}">\n'
                f"{prompt_content}\n"
                f"</prompt-file>\n"
            )

    outputs: dict[str, IOSpec] = output_specs if output_specs is not None else step_def.outputs
    output_paths = list(resolve_output_paths(outputs, variables).values())
    variables["step.outputs_list"] = ", ".join(output_paths)

    resolved_prompt = resolve_templates(prompt, variables)

    raw_outputs = step_def.outputs
    raw_output_paths = resolve_output_paths(raw_outputs, variables)
    resolved_output_paths = resolve_output_paths(outputs, variables)
    if resolved_output_paths and resolved_output_paths != raw_output_paths:
        output_contract = "\n".join(
            f"- {name}: {path}" for name, path in resolved_output_paths.items()
        )
        resolved_prompt = (
            f"{resolved_prompt.rstrip()}\n\n"
            "For this invocation, write outputs only to these exact paths:\n"
            f"{output_contract}\n"
            "These output paths override any default locations mentioned in the runbook "
            "or process docs. Do not modify the default runs/ tree for this invocation."
        )

    # Per-task agent session logs live under <run>/.logs/tasks/<step_id>/<item_key>/
    # for fan-out items, or under <run>/.logs/tasks/<step_id>/ for non-fan-out
    # steps. The run-level <run>/.logs/ branch holds parent-DAG events only.
    run_dir = compute_logs_dir(spec, variables).parent
    logs_dir = compute_task_logs_dir(run_dir, step_def, variables)
    if create_dirs:
        logs_dir.mkdir(parents=True, exist_ok=True)
    log_filename = compute_log_filename(step_def, variables)
    log_path = logs_dir / log_filename

    return resolved_prompt, logs_dir, log_path


def validate_step_inputs_exist(
    inputs: dict[str, IOSpec],
    variables: dict[str, str],
    *,
    context: str,
) -> None:
    """Resolve and verify runtime step inputs before dispatch."""
    for name, spec in inputs.items():
        if spec.kind == "stream":
            continue
        if not spec.path:
            raise ValueError(f"{context}: input '{name}' has no resolved path")

        resolved_path = resolve_templates(spec.path, variables)
        unresolved = check_unresolved_placeholders(
            resolved_path,
            context=f"{context} input '{name}'",
        )
        if unresolved:
            raise ValueError("; ".join(unresolved))

        path = Path(resolved_path)
        if not path.exists():
            if spec.optional:
                continue
            raise ValueError(f"{context}: input '{name}' not found: {resolved_path}")
        if spec.kind == "directory" and not path.is_dir():
            raise ValueError(f"{context}: input '{name}' is not a directory: {resolved_path}")
        if spec.kind == "file" and not path.is_file():
            raise ValueError(f"{context}: input '{name}' is not a file: {resolved_path}")


def join_filter_thread(lr: LaunchResult, timeout: float = 5.0) -> None:
    """Wait for the filter thread to finish flushing remaining lines.

    Must be called after the subprocess has exited (proc.wait() / proc.poll()
    returned non-None) and before running compaction.
    """
    if lr.filter_thread is not None:
        lr.filter_thread.join(timeout=timeout)


def _should_drop_line(line: str) -> bool:
    """Check whether a JSONL line is a cumulative streaming event that should be dropped.

    Fast path: if none of the drop-event names appear as a substring, return False
    immediately (zero-cost for the vast majority of lines).
    Slow path: parse JSON and check the top-level ``type`` field to avoid false
    positives from content fields that happen to contain the substring.
    """
    # Fast path: no substring match means definitely not a drop event.
    if "message_update" not in line and "tool_execution_update" not in line:
        return False
    # Slow path: parse and check top-level type.
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    return event.get("type") in _PI_LIVE_DROP_EVENTS


def start_log_filter_thread(pipe: IO[bytes], log_fh: IO[str]) -> threading.Thread:
    """Spawn a daemon thread that filters cumulative Pi streaming events from *pipe*
    and writes surviving lines to *log_fh* with per-line flush.

    The thread owns *log_fh* and closes it on EOF or error.
    """

    def _run() -> None:
        try:
            while True:
                raw = pipe.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                if _should_drop_line(line):
                    continue
                log_fh.write(line)
                log_fh.flush()
        except Exception:
            # Never lose data: copy remaining lines unfiltered.
            log.warning("Log filter thread error, falling back to unfiltered copy", exc_info=True)
            try:
                for raw in pipe:
                    log_fh.write(raw.decode("utf-8", errors="replace"))
                    log_fh.flush()
            except Exception:
                log.warning("Log filter fallback also failed", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                log_fh.close()

    thread = threading.Thread(target=_run, daemon=True, name="log-filter")
    thread.start()
    return thread


def launch_step(
    step_id: str,
    resolved_prompt: str,
    logs_dir: Path,
    log_path: Path,
    adapter_type: str,
    merged_config: dict[str, object],
    variables: dict[str, str],
    context_label: str,
    step_env: dict[str, str] | None = None,
) -> LaunchResult:
    """Launch a coding agent subprocess in detached mode."""
    adapter = get_adapter(adapter_type)

    ts = datetime.now(tz=UTC).strftime("%H%M%S")
    prompt_file = logs_dir / f"prompt-{step_id}-{context_label}-{ts}.txt"
    with atomic_output_file(prompt_file) as tmp_path:
        Path(tmp_path).write_text(resolved_prompt)

    env = adapter.prepare_env(dict(os.environ), merged_config)
    if step_env:
        env.update(step_env)
    cmd = adapter.build_command(prompt_file, merged_config, variables)
    cwd = adapter.working_directory(merged_config)

    use_filter = adapter_type == "pi-cli"
    log_fh = log_path.open("w")
    filter_thread: threading.Thread | None = None
    try:
        if use_filter:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            # Filter thread owns log_fh from this point.
            filter_thread = start_log_filter_thread(proc.stdout, log_fh)  # pyright: ignore[reportArgumentType]
        else:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception:
        if filter_thread is None:
            # Filter thread not started yet, we still own log_fh.
            log_fh.close()
        raise

    pid_filename = f"{step_id}_{context_label}.pid"
    pid_path = logs_dir / pid_filename
    with atomic_output_file(pid_path) as tmp_path:
        Path(tmp_path).write_text(str(proc.pid))

    return LaunchResult(
        proc=proc,
        pid=proc.pid,
        log_path=log_path,
        pid_path=pid_path,
        item=context_label,
        started_monotonic=time.monotonic(),
        timeout_s=int(str(merged_config["timeout_s"]))
        if merged_config.get("timeout_s") is not None
        else None,
        filter_thread=filter_thread,
    )
