"""Generic run-path resolution helpers.

These are workflow-agnostic utilities Metaproc itself uses to resolve the
generic ``RUNS_DIR`` runtime value. Workflow-specific resolvers (which
know about their own domain env vars and per-workflow overrides) live
in their owning workflow package and call into these helpers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from metaproc.config.env_vars import MetaprocEnv


class RunSettingsError(ValueError):
    """Raised when required run-directory settings are missing or invalid."""


def resolve_run_path(raw: str | Path, *, setting_name: str = "RUNS_DIR") -> Path:
    """Resolve a required run path to an absolute filesystem path."""
    value = str(raw).strip()
    if not value:
        raise RunSettingsError(f"{setting_name} is required and must not be empty.")
    return Path(value).expanduser().resolve()


def resolve_runtime_runs_dir(
    raw: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    required: bool = False,
) -> Path | None:
    """Resolve an explicit or environment ``RUNS_DIR`` to an absolute path."""
    if raw is not None and str(raw).strip():
        return resolve_run_path(raw, setting_name=MetaprocEnv.RUNS_DIR.name)
    source = os.environ if env is None else env
    value = source.get(MetaprocEnv.RUNS_DIR.name)
    if value is not None:
        stripped = value.strip()
        if stripped and stripped.lower() != "changeme":
            return resolve_run_path(stripped, setting_name=MetaprocEnv.RUNS_DIR.name)
    if required:
        raise RunSettingsError(
            "RUNS_DIR is required. Pass --var RUNS_DIR=/absolute/path or configure "
            "the workflow run settings before launching Metaproc."
        )
    return None
