"""Container entrypoint for the GCP Batch orchestrator.

Runs inside a GCP Batch VM with Filestore NFS mounted.  The orchestrator
executes the full ``run-process`` DAG, dispatching fan-out steps to
worker Batch jobs via ``--backend gcp-worker``.

Configuration via environment variables (set by ``orchestrator_dispatch.py``):

    METAPROC_PROCESS_SPEC     -- Path to .process.md spec file (relative to repo root)
    METAPROC_PROCESS_DIR      -- Legacy: process directory. Read-only fallback for
                                 pre-rename dispatch payloads.
    METAPROC_VARS             -- JSON-encoded dict of --var KEY=VALUE pairs
    METAPROC_NUM_WORKERS      -- Number of worker VMs for fan-out steps
    METAPROC_MACHINE_TYPE     -- GCP machine type for worker VMs
    METAPROC_MAX_CONCURRENCY  -- Per-pool concurrency limit for fan-out steps
    METAPROC_INITIAL_CONCURRENCY -- Starting concurrency for fan-out runpools
    METAPROC_SPOT             -- "true"/"false" for worker spot VMs
    METAPROC_VARIANT          -- Adapter/variant override
    METAPROC_ADAPTER_CONFIG   -- JSON-encoded adapter config overrides
    METAPROC_SKIP_STEPS       -- Comma-separated step IDs to skip
    METAPROC_FROM_STEP        -- Start from this step
    METAPROC_ONLY_STEP        -- Run only this step + downstream
    METAPROC_FORCE            -- "true"/"false" for --force
    METAPROC_CONTINUE_ON_ERROR -- "true"/"false" for --continue-on-error
    METAPROC_AUTH_ACCOUNT     -- Adapter for auth-pool dispatch (--auth-account)
    METAPROC_AUTH_BACKEND     -- Pool backend for --auth-backend
    METAPROC_AUTH_FALLBACK_POLICY -- --auth-fallback-policy value
    METAPROC_AUTH_INCLUDE_LABELS -- CSV → repeated --auth-include-labels
    METAPROC_AUTH_EXCLUDE_LABELS -- CSV → repeated --auth-exclude-labels
    METAPROC_RUN_BRANCH       -- Git branch to clone
    METAPROC_REPO_URL         -- Repository URL
    RUNS_DIR                  -- Base path for run artifacts (Filestore NFS mount)
    GH_TOKEN                  -- GitHub token for git operations

The entrypoint:
1. Calls ``bootstrap_container()`` for git clone + env setup
2. Runs ``metaproc run-process <process-spec> --backend gcp-worker [flags]``
   (without ``--cloud`` to avoid recursion)
3. Exits with the run-process exit code
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from metaproc.cloud.gcp.container_bootstrap import _run, bootstrap_container
from metaproc.commands.helpers import seed_runtime_vars
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.osutils.resource_context import log_resource_context

log = logging.getLogger(__name__)


def build_runprocess_cmd(
    *,
    resolved_process_spec: str,
    variables: dict[str, str],
    num_workers: str = "",
    machine_type: str = "",
    max_concurrency: str = "",
    initial_concurrency: str = "",
    spot: str = "true",
    variant: str = "",
    adapter_config_json: str = "",
    skip_steps: str = "",
    from_step: str = "",
    only_step: str = "",
    force: str = "false",
    continue_on_error: str = "false",
    auth_flags: AuthPoolFlags | None = None,
) -> list[str]:
    """Build the inner ``metaproc run-process`` command for the orchestrator.

    Pure function over the resolved env-var values. ``--cloud`` is deliberately
    omitted (the orchestrator is already inside the cloud path; passing it would
    recurse). Empty / falsy inputs are skipped so the command stays minimal and
    matches what an operator would type locally.
    """
    cmd = [
        "python",
        "-m",
        "metaproc",
        "run-process",
        resolved_process_spec,
        "--backend",
        "gcp-worker",
    ]

    for key, val in variables.items():
        cmd.extend(["--var", f"{key}={val}"])

    if num_workers:
        cmd.extend(["--num-workers", num_workers])

    if machine_type:
        cmd.extend(["--machine-type", machine_type])

    if max_concurrency:
        cmd.extend(["--max-concurrency", max_concurrency])

    if initial_concurrency:
        cmd.extend(["--initial-concurrency", initial_concurrency])

    if spot == "false":
        cmd.append("--no-spot")

    if variant:
        cmd.extend(["--variant", variant])

    if adapter_config_json:
        adapter_configs: dict[str, str] = json.loads(adapter_config_json)
        for key, val in adapter_configs.items():
            cmd.extend(["--adapter-config", f"{key}={val}"])

    if skip_steps:
        for step_id in skip_steps.split(","):
            cmd.extend(["--skip", step_id.strip()])

    if from_step:
        cmd.extend(["--from", from_step])

    if only_step:
        cmd.extend(["--only", only_step])

    if force == "true":
        cmd.append("--force")

    if continue_on_error == "true":
        cmd.append("--continue-on-error")

    if auth_flags is not None:
        cmd.extend(auth_flags.to_cli_flags())

    return cmd


def main() -> int:
    """Run the orchestrator entrypoint.  Returns exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Dump host + cgroup + rlimits + runtime + env at startup. See
    # metaproc/osutils/resource_context.py for what's collected and why.
    log_resource_context(log)

    process_spec = MetaprocEnv.METAPROC_PROCESS_SPEC.read_str(default="")
    if not process_spec:
        # Legacy fallback for pre-rename dispatch payloads.
        process_spec = MetaprocEnv.METAPROC_PROCESS_DIR.read_str(default="")
    vars_json = MetaprocEnv.METAPROC_VARS.read_str(default="{}")
    num_workers = MetaprocEnv.METAPROC_NUM_WORKERS.read_str(default="")
    machine_type = MetaprocEnv.METAPROC_MACHINE_TYPE.read_str(default="")
    max_concurrency = MetaprocEnv.METAPROC_MAX_CONCURRENCY.read_str(default="")
    initial_concurrency = MetaprocEnv.METAPROC_INITIAL_CONCURRENCY.read_str(default="")
    spot = MetaprocEnv.METAPROC_SPOT.read_str(default="true")
    variant = MetaprocEnv.METAPROC_VARIANT.read_str(default="")
    adapter_config_json = MetaprocEnv.METAPROC_ADAPTER_CONFIG.read_str(default="")
    skip_steps = MetaprocEnv.METAPROC_SKIP_STEPS.read_str(default="")
    from_step = MetaprocEnv.METAPROC_FROM_STEP.read_str(default="")
    only_step = MetaprocEnv.METAPROC_ONLY_STEP.read_str(default="")
    force = MetaprocEnv.METAPROC_FORCE.read_str(default="false")
    continue_on_error = MetaprocEnv.METAPROC_CONTINUE_ON_ERROR.read_str(default="false")
    # Phase 10 — single-source the env-var names via
    # AuthPoolFlags so a registry rename doesn't silently desync.
    auth_flags = AuthPoolFlags.from_env()

    if not process_spec:
        log.error("METAPROC_PROCESS_SPEC is required")
        return 1

    try:
        result = bootstrap_container()
    except RuntimeError as exc:
        log.error("Bootstrap failed: %s", exc)
        return 1

    work_dir = result.work_dir
    variables: dict[str, str] = json.loads(vars_json)

    seed_runtime_vars(variables)

    if not num_workers:
        log.warning(
            "METAPROC_NUM_WORKERS not set — inner run-process will use "
            "METAPROC_DEFAULT_NUM_WORKERS fallback or default to 1"
        )
    else:
        log.info("Propagating --num-workers %s to inner run-process", num_workers)

    resolved_process_spec = str(Path(work_dir) / process_spec)
    cmd = build_runprocess_cmd(
        resolved_process_spec=resolved_process_spec,
        variables=variables,
        num_workers=num_workers,
        machine_type=machine_type,
        max_concurrency=max_concurrency,
        initial_concurrency=initial_concurrency,
        spot=spot,
        variant=variant,
        adapter_config_json=adapter_config_json,
        skip_steps=skip_steps,
        from_step=from_step,
        only_step=only_step,
        force=force,
        continue_on_error=continue_on_error,
        auth_flags=auth_flags,
    )

    log.info("Running metaproc run-process for orchestrator")
    log.info("Command: %s", " ".join(cmd))

    exit_code = _run(cmd, cwd=work_dir)
    log.info("metaproc run-process exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
