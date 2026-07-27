"""Typed wrapper for the repo-sync env-var cohort.

The cohort drives ``container_bootstrap``'s repo clone + workspace
fetch. Each value used to be read individually from ``MetaprocEnv`` at
the same call site; wrapping it gives a single shape that read sites
can construct once.

Usage:

.. code-block:: python

    from metaproc.dispatch.repo_sync_payload import RepoSyncPayload

    payload = RepoSyncPayload.from_env()
    if payload.wheel_gcs:
        _install_wheel_from_gcs(payload.wheel_gcs, payload.wheel_sha256)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from metaproc.config.env_vars import MetaprocEnv


@dataclass(frozen=True)
class RepoSyncPayload:
    """Container-bootstrap repo + workspace sync inputs.

    Empty-string fields mean "unset". The bootstrap caller branches
    on truthiness (e.g. ``if payload.wheel_gcs: ...``) so empty fields
    are inert.
    """

    repo_url: str = ""
    run_branch: str = ""
    wheel_gcs: str = ""
    wheel_sha256: str = ""
    workspace_gcs: str = ""
    workspace_sha256: str = ""
    sparse_paths: str = ""
    workspace_packages: str = ""
    bundled_repo_dir: str = ""

    ENV_REPO_URL: ClassVar[str] = MetaprocEnv.METAPROC_REPO_URL.name
    ENV_RUN_BRANCH: ClassVar[str] = MetaprocEnv.METAPROC_RUN_BRANCH.name
    ENV_WHEEL_GCS: ClassVar[str] = MetaprocEnv.METAPROC_WHEEL_GCS.name
    ENV_WHEEL_SHA256: ClassVar[str] = MetaprocEnv.METAPROC_WHEEL_SHA256.name
    ENV_WORKSPACE_GCS: ClassVar[str] = MetaprocEnv.METAPROC_WORKSPACE_GCS.name
    ENV_WORKSPACE_SHA256: ClassVar[str] = MetaprocEnv.METAPROC_WORKSPACE_SHA256.name
    ENV_SPARSE_PATHS: ClassVar[str] = MetaprocEnv.METAPROC_REPO_SPARSE_PATHS.name
    ENV_WORKSPACE_PACKAGES: ClassVar[str] = MetaprocEnv.METAPROC_WORKSPACE_PACKAGES.name
    ENV_BUNDLED_REPO_DIR: ClassVar[str] = MetaprocEnv.METAPROC_BUNDLED_REPO_DIR.name

    @classmethod
    def all_env_var_names(cls) -> tuple[str, ...]:
        """Return all repo-sync env-var names in declaration order."""
        return (
            cls.ENV_REPO_URL,
            cls.ENV_RUN_BRANCH,
            cls.ENV_WHEEL_GCS,
            cls.ENV_WHEEL_SHA256,
            cls.ENV_WORKSPACE_GCS,
            cls.ENV_WORKSPACE_SHA256,
            cls.ENV_SPARSE_PATHS,
            cls.ENV_WORKSPACE_PACKAGES,
            cls.ENV_BUNDLED_REPO_DIR,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RepoSyncPayload:
        """Construct from process env (defaults to ``os.environ``).

        Mirrors :meth:`AuthPoolFlags.from_env`. The ``*_gcs`` fields
        get whitespace-stripped because operators sometimes paste
        ``gs://...`` URIs with stray newlines from terminal output.
        """

        e: Mapping[str, str] = env if env is not None else os.environ
        return cls(
            repo_url=e.get(cls.ENV_REPO_URL, ""),
            run_branch=e.get(cls.ENV_RUN_BRANCH, ""),
            wheel_gcs=e.get(cls.ENV_WHEEL_GCS, "").strip(),
            wheel_sha256=e.get(cls.ENV_WHEEL_SHA256, "").strip(),
            workspace_gcs=e.get(cls.ENV_WORKSPACE_GCS, "").strip(),
            workspace_sha256=e.get(cls.ENV_WORKSPACE_SHA256, "").strip(),
            sparse_paths=e.get(cls.ENV_SPARSE_PATHS, "").strip(),
            workspace_packages=e.get(cls.ENV_WORKSPACE_PACKAGES, "").strip(),
            bundled_repo_dir=e.get(cls.ENV_BUNDLED_REPO_DIR, "").strip(),
        )

    def to_env_vars(self) -> dict[str, str]:
        """Encode for a Batch job env_vars dict.

        Empty-string fields are OMITTED so a baseline call with no
        repo-sync configured emits no env vars.
        """
        out: dict[str, str] = {}
        if self.repo_url:
            out[self.ENV_REPO_URL] = self.repo_url
        if self.run_branch:
            out[self.ENV_RUN_BRANCH] = self.run_branch
        if self.wheel_gcs:
            out[self.ENV_WHEEL_GCS] = self.wheel_gcs
        if self.wheel_sha256:
            out[self.ENV_WHEEL_SHA256] = self.wheel_sha256
        if self.workspace_gcs:
            out[self.ENV_WORKSPACE_GCS] = self.workspace_gcs
        if self.workspace_sha256:
            out[self.ENV_WORKSPACE_SHA256] = self.workspace_sha256
        if self.sparse_paths:
            out[self.ENV_SPARSE_PATHS] = self.sparse_paths
        if self.workspace_packages:
            out[self.ENV_WORKSPACE_PACKAGES] = self.workspace_packages
        if self.bundled_repo_dir:
            out[self.ENV_BUNDLED_REPO_DIR] = self.bundled_repo_dir
        return out


__all__ = ["RepoSyncPayload"]
