"""metaproc run-step — launch a single step."""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer
from strif import atomic_output_file

from metaproc.adapters.base import Adapter
from metaproc.adapters.registry import derive_variant, get_adapter
from metaproc.cli import app, get_output
from metaproc.commands.helpers import (
    enforce_no_unresolved_placeholders,
    enrich_single_item,
    find_step_def,
    load_process_spec,
    parse_adapter_config,
    parse_var_args,
    require_runtime_runs_dir,
    resolve_process_path,
    resolve_record_output_paths,
    seed_runtime_vars,
)
from metaproc.config.env_vars import MetaprocEnv
from metaproc.engine.build_plan import build_plan, merge_defaults
from metaproc.engine.code_handler import resolve_code_handler
from metaproc.engine.dep_state import fingerprint_step
from metaproc.engine.input_validation import validate_process_inputs
from metaproc.engine.pathing import compute_run_dir, compute_task_state_dir
from metaproc.engine.placeholders import (
    collect_step_runtime_placeholders,
    resolve_runtime_config,
    resolve_templates,
    validate_spec_placeholders,
)
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.resource_sampling import run_sampled_step_command, sample_step_resources
from metaproc.engine.runtime import (
    launch_step,
    prepare_step,
    start_log_filter_thread,
    validate_step_inputs_exist,
)
from metaproc.engine.validation import validate_item_outputs
from metaproc.errors import CLIError, ValidationError
from metaproc.io.state_io import (
    compute_item_dir,
    mark_completed_at,
    mark_failed_at,
    mark_running_at,
    read_status_at,
    write_attempt_at,
    write_manual_ack_at,
    write_result_at,
)
from metaproc.logutil.compaction import try_compact_log
from metaproc.models.runtime import AttemptRecord, ManualAckRecord, ResultRecord
from metaproc.paths import MANUAL_ACK_FILE


@app.command("run-step")
def run_step(
    process_spec: Path = typer.Argument(..., help="Path to .process.md spec file"),
    step: str = typer.Option(..., "--step", help="Step ID to run"),
    var: list[str] = typer.Option([], "--var", help="KEY=VALUE parameters"),
    adapter: str | None = typer.Option(None, "--adapter", help="Override adapter type"),  # noqa: UP007
    adapter_config: list[str] = typer.Option(
        [], "--adapter-config", help="KEY=VALUE adapter config overrides"
    ),
    item: str | None = typer.Option(
        None, "--item", help="Single item for for_each steps (e.g. --item AAPL)"
    ),  # noqa: UP007
    variant: str | None = typer.Option(None, "--variant", help="Variant directory name"),  # noqa: UP007
    dry_run: bool = typer.Option(False, "--dry-run", help="Print launch config without spawning"),
    wait: bool = typer.Option(False, "--wait", help="Block until subprocess exits"),
    operator: str | None = typer.Option(  # noqa: UP007
        None, "--operator", help="Operator identity for manual-step acknowledgment"
    ),
    no_validate: bool = typer.Option(
        False,
        "--no-validate",
        help=(
            "Skip process-level input validation. Per-step input existence "
            "is still enforced at dispatch. Use only when a single step is "
            "being run in isolation against a hand-crafted fixture."
        ),
    ),
) -> None:
    """Execute a single pipeline step via subprocess."""
    out = get_output()

    process_path = resolve_process_path(process_spec)
    process_dir = process_path.parent

    spec = load_process_spec(process_path)
    variables = seed_runtime_vars(parse_var_args(var))
    variables = expand_process_vars(spec, variables, process_dir=process_path.parent)
    require_runtime_runs_dir(variables, command="run-step")
    config_overrides = parse_adapter_config(adapter_config)

    placeholder_errors = validate_spec_placeholders(spec, variables)
    if placeholder_errors:
        msg = (
            "unresolved placeholders in process spec (pass via --var or set env var):\n  "
            + "\n  ".join(placeholder_errors)
        )
        raise CLIError(msg)

    if not no_validate:
        input_errors = validate_process_inputs(spec, variables, process_dir)
        if input_errors:
            msg = "process input validation failed (pass --no-validate to skip):\n  " + "\n  ".join(
                input_errors
            )
            raise CLIError(msg)

    try:
        resolved = build_plan(
            spec,
            variables,
            process_path=process_path,
            adapter_override=adapter,
            config_overrides=config_overrides or None,
            validate_required_inputs=not no_validate,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    target = next((s for s in resolved.steps if s.step_id == step), None)
    if target is None:
        available = [s.step_id for s in resolved.steps]
        raise CLIError(f"step '{step}' not found. Available: {', '.join(available)}")

    step_def = find_step_def(spec, step)
    merged = merge_defaults(step_def, spec.defaults)
    effective_outputs = target.outputs
    step_hash = fingerprint_step(target)

    # ── for_each item enrichment ──────────────────────────────────────
    if step_def.for_each and item:
        enriched = enrich_single_item(
            step,
            step_def,
            item,
            variables,
            target.inputs,
            target.fan_out.source if target.fan_out is not None else None,
        )
        variables.update(enriched)
    elif step_def.for_each and not item:
        from metaproc.engine.discovery import (  # noqa: PLC0415 -- pre-existing local import; needs review
            normalize_item_fields,
        )

        item_fields = normalize_item_fields(step_def)
        missing = [f for f in item_fields if f not in variables]
        if missing:
            raise CLIError(
                f"step '{step}' uses for_each and requires item fields "
                f"{', '.join(missing)}; use --item to select a single item from the "
                f"items file, pass the fields via --var, or use run-parallel instead."
            )

    if target.mode == "composite":
        child_path_raw = target.uses_path or step_def.uses
        if child_path_raw:
            child_path = Path(child_path_raw).resolve()
            raise CLIError(
                f"step '{step}' is mode:composite and cannot be executed with run-step; "
                f"run the child process file instead: {child_path}"
            )
        raise CLIError(f"step '{step}' is mode:composite and cannot be executed with run-step")

    if target.mode == "manual":
        try:
            validate_step_inputs_exist(target.inputs, variables, context=f"step '{step}'")
        except ValueError as exc:
            raise CLIError(str(exc)) from exc

        run_dir = compute_run_dir(spec, variables)
        state_dir = compute_task_state_dir(run_dir, step_def, variables)
        artifact_dir = compute_item_dir(effective_outputs, variables) or (run_dir / step)

        operator_name = (
            operator
            or MetaprocEnv.METAPROC_OPERATOR.read_str(default=None)
            or MetaprocEnv.USER.read_str(default=None)
            or MetaprocEnv.USERNAME.read_str(default=None)
            or "unknown"
        )

        if dry_run:
            out.data(
                "=== DRY RUN ===\n"
                f"Step: {step}\n"
                "Mode: manual\n"
                f"Operator: {operator_name}\n"
                f"Ack path: {state_dir / MANUAL_ACK_FILE}"
            )
            return

        state_dir.mkdir(parents=True, exist_ok=True)
        run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
        run_id = f"{spec.name}/{run_context}"
        item_record = {"step": step}
        current_status = read_status_at(state_dir)
        running_record = (
            current_status if current_status and current_status.state == "running" else None
        )
        if running_record is None and (
            current_status is None or current_status.state != "completed"
        ):
            running_record = mark_running_at(
                state_dir, run_id=run_id, step_id=step, item=item_record
            )

        write_manual_ack_at(
            state_dir,
            ManualAckRecord(
                run_id=run_id,
                step_id=step,
                operator=operator_name,
                acknowledged_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )

        if effective_outputs:
            output_errors = validate_item_outputs(
                artifact_dir, effective_outputs, variables=variables
            )
            if output_errors:
                mark_failed_at(
                    state_dir,
                    error=f"output validation failed: {'; '.join(output_errors)}",
                    running_record=running_record,
                )
                raise CLIError(
                    f"step '{step}' output validation failed: {'; '.join(output_errors)}"
                )

        completed = mark_completed_at(state_dir, running_record=running_record)
        write_result_at(
            state_dir,
            ResultRecord(
                run_id=run_id,
                step_id=step,
                state="completed",
                validated=True,
                outputs=resolve_record_output_paths(effective_outputs, variables),
                published_at=completed.completed_at
                or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                step_hash=step_hash,
            ),
        )
        out.progress(f"manual step '{step}' acknowledged by {operator_name}")
        return

    adapter_type = target.adapter.type
    merged_config = dict(target.adapter.config)
    if merged.get("max_budget_usd") is not None:
        merged_config.setdefault("max_budget_usd", merged["max_budget_usd"])
    runtime_config = cast("dict[str, object]", resolve_runtime_config(merged_config, variables))

    derived_variant = derive_variant(adapter_type, runtime_config)
    effective_execution_profile = target.execution_profile or adapter or derived_variant
    effective_variant = (
        target.artifact_namespace
        or step_def.artifact_namespace
        or variant
        or step_def.variant
        or derived_variant
    )
    variables["EXECUTION_PROFILE"] = effective_execution_profile
    variables["ARTIFACT_NAMESPACE"] = effective_variant
    variables["VARIANT"] = effective_variant

    # ── mode: code branch ──────────────────────────────────────────
    if target.mode == "code":
        try:
            validate_step_inputs_exist(target.inputs, variables, context=f"step '{step}'")
        except ValueError as exc:
            raise CLIError(str(exc)) from exc
        handler_ref = target.handler
        command_ref = target.command

        if not handler_ref and not command_ref:
            raise CLIError(f"step '{step}' is mode:code but has no handler or command")

        handler_fn = None
        if handler_ref:
            try:
                handler_fn = resolve_code_handler(handler_ref, process_dir)
            except (ValueError, FileNotFoundError, ImportError, AttributeError, TypeError) as exc:
                raise ValidationError(str(exc)) from exc

        if dry_run:
            if handler_ref:
                resolved_handler_path = (process_dir / handler_ref.rsplit(":", 1)[0]).resolve()
                out.data(
                    f"=== DRY RUN ===\nStep: {step}\nMode: code\nVariant: {effective_variant}\nHandler: {handler_ref} ({resolved_handler_path})"
                )
            else:
                out.data(
                    f"=== DRY RUN ===\nStep: {step}\nMode: code\nVariant: {effective_variant}\nCommand: {command_ref}"
                )
            return

        run_dir = compute_run_dir(spec, variables)
        state_dir = compute_task_state_dir(run_dir, step_def, variables)
        artifact_dir = compute_item_dir(effective_outputs, variables)
        run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
        run_id = f"{spec.name}/{run_context}"
        for_each_def = step_def.for_each
        each_var = for_each_def.bind if for_each_def else None
        each_label = variables.get(each_var, "default") if each_var else None
        canonical_item_key = state_dir.name if for_each_def else None
        item_record = {each_var: each_label} if each_var and each_label else {"step": step}

        runtime_info: dict[str, object] = {"mode": "code", "variant": effective_variant}
        if handler_ref:
            runtime_info["handler"] = handler_ref
        else:
            runtime_info["command"] = command_ref

        state_dir.mkdir(parents=True, exist_ok=True)
        write_attempt_at(
            state_dir,
            AttemptRecord(
                run_id=run_id,
                step_id=step,
                item=item_record,
                params=dict(variables),
                outputs=resolve_record_output_paths(effective_outputs, variables),
                runtime=runtime_info,
                step_hash=step_hash,
            ),
        )
        mark_running_at(state_dir, run_id=run_id, step_id=step, item=item_record)

        try:
            if handler_ref:
                if handler_fn is None:
                    raise CLIError(f"handler '{handler_ref}' resolved to None for step '{step}'")
                process_step = step_def.model_copy(
                    deep=True,
                    update={"inputs": target.inputs, "outputs": target.outputs},
                )
                with sample_step_resources(
                    run_dir=run_dir,
                    run_id=run_id,
                    step_node_id=step,
                    item_key=canonical_item_key,
                ):
                    handler_fn(dict(variables), process_step)
            else:
                if command_ref is None:
                    raise CLIError(f"no command or handler configured for step '{step}'")
                resolved_cmd = resolve_templates(command_ref, variables)
                env = dict(os.environ)
                env.update(
                    {
                        key: resolve_templates(value, variables)
                        for key, value in (target.env or {}).items()
                    }
                )
                proc = run_sampled_step_command(
                    shlex.split(resolved_cmd),
                    env=env,
                    cwd=process_dir,
                    run_dir=run_dir,
                    run_id=run_id,
                    step_node_id=step,
                    item_key=canonical_item_key,
                )
                if proc.stdout:
                    out.progress(proc.stdout.rstrip())
        except subprocess.CalledProcessError as exc:
            mark_failed_at(state_dir, error=f"command exit code {exc.returncode}")
            raise CLIError(f"step '{step}' command failed (exit {exc.returncode})") from exc
        except Exception as exc:
            mark_failed_at(state_dir, error=str(exc))
            raise CLIError(f"step '{step}' code execution failed: {exc}") from exc

        exit_code = 0
        if effective_outputs and artifact_dir is not None:
            output_errors = validate_item_outputs(
                artifact_dir, effective_outputs, variables=variables
            )
            if output_errors:
                mark_failed_at(
                    state_dir, error=f"output validation failed: {'; '.join(output_errors)}"
                )
                exit_code = 1
            else:
                mark_completed_at(state_dir)
                write_result_at(
                    state_dir,
                    ResultRecord(
                        run_id=run_id,
                        step_id=step,
                        state="completed",
                        validated=True,
                        outputs=resolve_record_output_paths(effective_outputs, variables),
                        published_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                        step_hash=step_hash,
                    ),
                )
        else:
            mark_completed_at(state_dir)
        out.progress(f"Step '{step}' completed (code mode)")
        raise typer.Exit(code=exit_code)

    # ── mode: agent branch ─────────────────────────────────────────
    try:
        adapter_obj: Adapter = get_adapter(adapter_type)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    step_outputs = effective_outputs or None

    try:
        validate_step_inputs_exist(target.inputs, variables, context=f"step '{step}'")
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    resolved_prompt, logs_dir, log_path = prepare_step(
        spec,
        step_def,
        variables,
        process_dir,
        step_outputs,
        prompt_paths=target.prompt_paths,
    )

    allowed_runtime = collect_step_runtime_placeholders(step_def)
    optional_unset = {name for name in spec.optional_input_names if name not in variables}
    enforce_no_unresolved_placeholders(
        resolved_prompt, context=f"step '{step}' prompt", allowed=allowed_runtime | optional_unset
    )

    for_each = step_def.for_each
    each = for_each.bind if for_each else None
    if each:
        context_label = variables.get(each, "default")
    else:
        context_label = variables.get("DATE", variables.get("SCOPE", "ctx"))

    if dry_run:
        dummy_prompt_file = Path("<prompt-file>")
        display_cmd = adapter_obj.build_command(dummy_prompt_file, runtime_config, variables)
        out.data(
            f"=== DRY RUN ===\nStep: {step}\nAdapter: {adapter_type}\n"
            f"Variant: {effective_variant}\nLog: {log_path}\n"
            f"Prompt ({len(resolved_prompt)} chars):\n{resolved_prompt.strip()}\n"
            f"Command: {' '.join(display_cmd)}"
        )
        return

    run_dir = compute_run_dir(spec, variables)
    state_dir = compute_task_state_dir(run_dir, step_def, variables)
    artifact_dir = compute_item_dir(effective_outputs, variables)
    run_context = variables.get("RUN_ID", variables.get("DATE", variables.get("SCOPE", "")))
    run_id = f"{spec.name}/{run_context}"

    def _write_attempt() -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        cmd = adapter_obj.build_command(Path("<prompt-file>"), runtime_config, variables)
        attempt_runtime: dict[str, object] = {
            "adapter_type": adapter_type,
            "model": runtime_config.get("model") or getattr(adapter_obj, "default_model", None),
            "variant": effective_variant,
            "command": cmd,
        }
        write_attempt_at(
            state_dir,
            AttemptRecord(
                run_id=run_id,
                step_id=step,
                item={each: context_label} if each else {},
                params=dict(variables),
                outputs=resolve_record_output_paths(effective_outputs, variables),
                runtime=attempt_runtime,
                step_hash=step_hash,
            ),
        )

    if wait:
        out.progress(f"Running step '{step}' (wait mode)...")
        out.progress(f"Log: {log_path}")

        ts = datetime.now(tz=UTC).strftime("%H%M%S")
        prompt_file = logs_dir / f"prompt-{step}-{context_label}-{ts}.txt"
        with atomic_output_file(prompt_file) as tmp_path:
            Path(tmp_path).write_text(resolved_prompt)

        _write_attempt()
        running_record = mark_running_at(
            state_dir,
            run_id=run_id,
            step_id=step,
            item={each: context_label} if each else {},
        )

        env = adapter_obj.prepare_env(dict(os.environ), runtime_config)
        if target.env:
            env.update(
                {key: resolve_templates(value, variables) for key, value in target.env.items()}
            )
        cmd = adapter_obj.build_command(prompt_file, runtime_config, variables)
        cwd = adapter_obj.working_directory(runtime_config)
        timeout_s = runtime_config.get("timeout_s")

        use_filter = adapter_type == "pi-cli"
        timeout_val = int(str(timeout_s)) if timeout_s is not None else None
        try:
            if use_filter:
                with log_path.open("w", encoding="utf-8") as log_fh:
                    fg_proc = subprocess.Popen(
                        cmd,
                        env=env,
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    )
                    ft = start_log_filter_thread(fg_proc.stdout, log_fh)  # pyright: ignore[reportArgumentType]
                    fg_proc.wait(timeout=timeout_val)
                    ft.join(timeout=5.0)
            else:
                with log_path.open("w", encoding="utf-8") as log_fh:
                    fg_proc = subprocess.run(
                        cmd,
                        env=env,
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                        check=False,
                        timeout=timeout_val,
                    )
        except subprocess.TimeoutExpired:
            mark_failed_at(
                state_dir, error=f"timeout after {timeout_s}s", running_record=running_record
            )
            raise CLIError(f"step '{step}' timed out after {timeout_s}s") from None

        exit_code = fg_proc.returncode
        if fg_proc.returncode == 0:
            if effective_outputs and artifact_dir is not None:
                output_errors = validate_item_outputs(
                    artifact_dir, effective_outputs, variables=variables
                )
            else:
                output_errors = []
            if output_errors:
                mark_failed_at(
                    state_dir,
                    error=f"output validation failed: {'; '.join(output_errors)}",
                    running_record=running_record,
                )
                exit_code = 1
            else:
                mark_completed_at(state_dir, running_record=running_record)
                write_result_at(
                    state_dir,
                    ResultRecord(
                        run_id=run_id,
                        step_id=step,
                        state="completed",
                        validated=True,
                        outputs=resolve_record_output_paths(effective_outputs, variables),
                        published_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                        step_hash=step_hash,
                    ),
                )
        else:
            mark_failed_at(
                state_dir, error=f"exit code {fg_proc.returncode}", running_record=running_record
            )

        # Compact log after process exit (removes streaming noise).
        try_compact_log(log_path)

        out.progress(f"Step '{step}' exited with code {exit_code}")
        raise typer.Exit(code=exit_code)

    # Detached mode
    _write_attempt()
    result = launch_step(
        step,
        resolved_prompt,
        logs_dir,
        log_path,
        adapter_type,
        runtime_config,
        variables,
        context_label,
        step_env=target.env,
    )
    out.data(f"Launched step '{step}' (PID {result.pid})")
    out.progress(f"Log: {result.log_path}")
    out.progress(f"PID file: {result.pid_path}")
