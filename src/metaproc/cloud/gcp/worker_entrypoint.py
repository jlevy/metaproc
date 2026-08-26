"""Unified container entrypoint for GCP Batch worker execution.

This is the sole entrypoint for all GCP Batch worker containers.  Each worker
VM receives a partition of items via ``run-process --backend gcp-worker``
(worker_dispatch) and runs them locally via RunPool.

Configuration via environment variables:

    METAPROC_WORKER_ITEMS     -- Comma-separated item IDs
    METAPROC_PROCESS_SPEC     -- Path to .process.md spec file (relative to repo root)
    METAPROC_PROCESS_DIR      -- Legacy: process directory. Read-only fallback for
                                 pre-rename dispatch payloads.
    METAPROC_STEP             -- Step ID to run
    METAPROC_VARS             -- JSON-encoded dict of --var KEY=VALUE pairs
    METAPROC_MAX_CONCURRENCY  -- Max concurrent subprocesses (default: 1)
    METAPROC_INITIAL_CONCURRENCY -- Starting subprocess concurrency
    METAPROC_RUN_BRANCH       -- Git branch to clone
    METAPROC_REPO_URL         -- Repository URL
    RUNS_DIR                  -- Base path for run artifacts (Filestore NFS mount)
    GH_TOKEN                  -- GitHub token for git operations

Auth-pool dispatch:

    METAPROC_AUTH_ACCOUNT         -- Adapter for auth-pool dispatch (--auth-account)
    METAPROC_AUTH_BACKEND         -- Pool backend for --auth-backend
    METAPROC_AUTH_FALLBACK_POLICY -- --auth-fallback-policy value
    METAPROC_AUTH_INCLUDE_LABELS  -- CSV -> repeated --auth-include-labels
    METAPROC_AUTH_EXCLUDE_LABELS  -- CSV -> repeated --auth-exclude-labels

When METAPROC_AUTH_ACCOUNT is set, the inner run-parallel command receives
the matching --auth-* flags and constructs a PoolDispatchConfig (mirrors
the local run-process surface). Without these, run-parallel falls back to
the legacy single-credential bootstrap path. The orchestrator-leg sets
these env vars; worker_dispatch propagates them onto each fan-out worker
Batch job's environment.

The entrypoint:
1. Calls ``bootstrap_container()`` for git clone + env setup
2. Runs ``metaproc run-parallel --backend local --items <items> [--auth-*]``
3. Exits with the run-parallel exit code (outputs are on NFS)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cloud.gcp.container_bootstrap import _run, bootstrap_container
from metaproc.cloud.gcp.secret_hydration import hydrate_secret_env
from metaproc.commands.helpers import seed_runtime_vars
from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags
from metaproc.osutils.resource_context import log_resource_context

log = logging.getLogger(__name__)


def arm_legacy_bootstrap_guard(
    auth_flags: AuthPoolFlags,
    *,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Set ``METAPROC_AUTH_POOL_RUN=1`` before per-adapter ``bootstrap(home)``.

    When auth-pool dispatch is enabled, each adapter's existing
    pool-aware ``bootstrap(home)`` guard becomes a no-op. Without this,
    ``CLAUDE_CODE_CREDS_JSON`` (if propagated by legacy callers or an
    operator override) would still materialize
    ``~/.claude/.credentials.json`` at worker entry, competing with the
    per-slot Vehicle A/B scoping at item launch. Legacy no-auth
    dispatches (``auth_account`` empty) skip this set so the
    single-credential bootstrap path stays intact.
    """
    if auth_flags.is_pool_dispatch_enabled():
        target_env = os.environ if env is None else env
        target_env[MetaprocEnv.METAPROC_AUTH_POOL_RUN.name] = "1"


def main() -> int:
    """Run the worker entrypoint.  Returns exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        hydrated = hydrate_secret_env()
    except RuntimeError as exc:
        log.error("Secret hydration failed: %s", exc)
        return 1
    if hydrated:
        log.info("Hydrated secret environment variables: %s", ", ".join(hydrated))

    # Dump the full resource envelope (host, cgroup, rlimits, runtime, env)
    # before any work. When a worker OOM-kills mid-run, this is the first
    # thing to look at — the cgroup memory_limit reveals whether the Batch
    # compute_resource was right-sized for the VM.
    log_resource_context(log)

    items = MetaprocEnv.METAPROC_WORKER_ITEMS.read_str(default="")
    process_spec = MetaprocEnv.METAPROC_PROCESS_SPEC.read_str(default="")
    if not process_spec:
        # Legacy fallback for pre-rename dispatch payloads.
        process_spec = MetaprocEnv.METAPROC_PROCESS_DIR.read_str(default="")
    step = MetaprocEnv.METAPROC_STEP.read_str(default="")
    vars_json = MetaprocEnv.METAPROC_VARS.read_str(default="{}")
    max_concurrency = MetaprocEnv.METAPROC_MAX_CONCURRENCY.read_str(default="")
    initial_concurrency = MetaprocEnv.METAPROC_INITIAL_CONCURRENCY.read_str(default="")
    adapter_config_json = MetaprocEnv.METAPROC_ADAPTER_CONFIG.read_str(default="")
    variant = MetaprocEnv.METAPROC_VARIANT.read_str(default="")
    max_retries = MetaprocEnv.METAPROC_MAX_RETRIES.read_str(default="")
    # The orchestrator propagates these auth-pool env vars via
    # worker_dispatch.py; without reading them here the worker would
    # silently fall back to the legacy single-credential bootstrap even
    # when the operator passed --auth-* on the outer `run-process
    # --cloud` command. Single-sourced through AuthPoolFlags so a
    # MetaprocEnv rename does not silently desync this site.
    auth_flags = AuthPoolFlags.from_env()
    arm_legacy_bootstrap_guard(auth_flags)

    if not items:
        log.error("METAPROC_WORKER_ITEMS is required")
        return 1
    if not process_spec:
        log.error("METAPROC_PROCESS_SPEC is required")
        return 1
    if not step:
        log.error("METAPROC_STEP is required")
        return 1

    try:
        result = bootstrap_container()
    except RuntimeError as exc:
        log.error("Bootstrap failed: %s", exc)
        return 1

    # Run adapter bootstrap hooks so adapters can materialize per-task
    # filesystem state (e.g., Claude Code's ~/.claude/.credentials.json from
    # CLAUDE_CODE_CREDS_JSON injected via Secret Manager) before the
    # run-parallel subprocess is launched. Each adapter's default is a no-op.
    home = Path.home()
    for adapter in ADAPTER_REGISTRY.values():
        adapter.bootstrap(home)

    work_dir = result.work_dir
    variables: dict[str, str] = json.loads(vars_json)

    seed_runtime_vars(variables)

    resolved_process_spec = os.path.join(work_dir, process_spec)
    cmd = build_runparallel_cmd(
        resolved_process_spec=resolved_process_spec,
        step=step,
        items=items,
        variables=variables,
        max_concurrency=max_concurrency,
        initial_concurrency=initial_concurrency,
        variant=variant,
        max_retries=max_retries,
        adapter_config_json=adapter_config_json,
        auth_flags=auth_flags,
    )

    log.info(
        "Running metaproc run-parallel with %d items, max_concurrency=%s, auth_account=%s",
        len(items.split(",")),
        max_concurrency or "default",
        auth_flags.auth_account or "(none — legacy single-credential bootstrap)",
    )
    log.info("Command: %s", " ".join(cmd))

    exit_code = _run(cmd, cwd=work_dir)
    log.info("metaproc run-parallel exited with code %d", exit_code)
    return exit_code


def build_runparallel_cmd(
    *,
    resolved_process_spec: str,
    step: str,
    items: str,
    variables: dict[str, str],
    max_concurrency: str = "",
    initial_concurrency: str = "",
    variant: str = "",
    max_retries: str = "",
    adapter_config_json: str = "",
    auth_flags: AuthPoolFlags | None = None,
) -> list[str]:
    """Build the inner ``metaproc run-parallel`` command for the worker.

    Pure function over resolved env-var values, extracted from
    :func:`main` so it can be unit-tested without bootstrapping a
    container. Mirrors
    :func:`metaproc.cloud.gcp.orchestrator_entrypoint.build_runprocess_cmd`
    for the worker leg.

    ``auth_flags`` is the consolidated five-flag :class:`AuthPoolFlags`
    shape. When ``None`` or with ``auth_account == ""`` the ``--auth-*``
    block is skipped and the worker falls back to the legacy
    single-credential bootstrap path. When set, the worker constructs a
    :class:`PoolDispatchConfig` matching whatever the orchestrator was
    given.
    """
    cmd = [
        "python",
        "-m",
        "metaproc",
        "run-parallel",
        resolved_process_spec,
        "--step",
        step,
        "--items",
        items,
        "--backend",
        "local",
    ]

    for key, val in variables.items():
        cmd.extend(["--var", f"{key}={val}"])

    if max_concurrency:
        cmd.extend(["--max-concurrency", max_concurrency])

    if initial_concurrency:
        cmd.extend(["--initial-concurrency", initial_concurrency])

    if variant:
        cmd.extend(["--variant", variant])

    if max_retries:
        cmd.extend(["--max-retries", max_retries])

    if adapter_config_json:
        adapter_configs: dict[str, Any] = json.loads(adapter_config_json)
        for key, val in adapter_configs.items():
            # `--adapter-config KEY=VALUE` is parsed string-only on the
            # receiving side (parse_adapter_config + _coerce_config_value).
            # If we let f-string do Python repr on a list, the receiver sees
            # "['Read', 'Write', ...]" and the comma-split coercion ends up
            # with "['Read'", "'Write'", ... — each name wrapped in stray
            # quote and bracket chars. The receiving CLI can no longer match
            # the requested tools and may fall back to an unintended tool
            # surface. Coerce list/bool here so the round-trip is clean.
            if isinstance(val, list):
                serialized: str = ",".join(str(t) for t in val)
            elif isinstance(val, bool):
                serialized = "true" if val else "false"
            else:
                serialized = str(val)
            cmd.extend(["--adapter-config", f"{key}={serialized}"])

    if auth_flags is not None:
        cmd.extend(auth_flags.to_cli_flags())

    return cmd


if __name__ == "__main__":
    sys.exit(main())
