"""Single-task container entrypoint for ``metaproc gcp run``.

Runs inside a GCP Batch task launched by ``gcp_run_dispatch.py``. Unlike
``worker_entrypoint`` (which spins up RunPool over a partition of items),
this entrypoint is one-shot: install the wheel, extract the workspace,
exec the user command, propagate the exit code.

Contract: all configuration uses env vars set by ``gcp_run_dispatch``:

    METAPROC_WHEEL_GCS         (optional) gs:// URI to a current-branch
                               metaproc wheel; if set, downloaded and
                               force-reinstalled into ``/opt/venv`` before
                               the user command runs.
    METAPROC_WHEEL_SHA256      required SHA-256 digest when
                               ``METAPROC_WHEEL_GCS`` is set.
    METAPROC_WORKSPACE_GCS     (optional) gs:// URI to a workspace tar.gz;
                               if set, downloaded and extracted into
                               ``/workspace/`` before the user command.
    METAPROC_WORKSPACE_SHA256  required SHA-256 digest when
                               ``METAPROC_WORKSPACE_GCS`` is set.
    METAPROC_WORKSPACE_PACKAGES optional comma-separated repository-relative
                               Python package paths installed editable after
                               workspace extraction.
    CLAUDE_CODE_CREDS_JSON     (optional) Personal-Plan OAuth blob; the
                               claude_code adapter's ``bootstrap()`` hook
                               materializes it to ``~/.claude/.credentials.json``.
    CODEX_CREDS_JSON           (optional) ChatGPT-OAuth blob (Vehicle B); the
                               codex adapter's ``bootstrap()`` hook
                               materializes it to ``~/.codex/auth.json``.
    OPENAI_API_KEY             (optional) Codex Vehicle A and pi-cli openai
                               provider. Passed through to the user command
                               env as-is.
    METAPROC_GCP_RUN_CMD       JSON-encoded argv list for the user command.

After bootstrap, ``cd`` to the directory returned by
``bootstrap_gcp_run()`` (``/workspace`` when a workspace tarball was
extracted, otherwise ``/tmp``) and ``os.execvp`` the user command. The
task's exit code is the user command's exit code.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.cloud.gcp.container_bootstrap import bootstrap_gcp_run
from metaproc.config.env_vars import MetaprocEnv

log = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"
FALLBACK_WORK_DIR = "/tmp"


def main() -> int:
    """Entrypoint: bootstrap, then exec the user command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cmd_json = MetaprocEnv.METAPROC_GCP_RUN_CMD.read_str(default="")
    if not cmd_json:
        log.error("METAPROC_GCP_RUN_CMD is required")
        return 2

    try:
        decoded = json.loads(cmd_json)
    except json.JSONDecodeError as exc:
        log.error("METAPROC_GCP_RUN_CMD is not valid JSON: %s", exc)
        return 2
    if not isinstance(decoded, list) or not all(isinstance(a, str) for a in decoded):
        log.error("METAPROC_GCP_RUN_CMD must be a JSON list of strings, got %r", decoded)
        return 2
    argv: list[str] = list(decoded)
    if not argv:
        log.error("METAPROC_GCP_RUN_CMD argv is empty")
        return 2

    try:
        work_dir = bootstrap_gcp_run(home=Path.home(), env=os.environ)
    except RuntimeError as exc:
        log.error("Bootstrap failed: %s", exc)
        return 1

    # Adapter bootstrap hooks materialize per-task filesystem state
    # (e.g. claude_code's ~/.claude/.credentials.json from
    # CLAUDE_CODE_CREDS_JSON) before exec.
    home = Path.home()
    for adapter in ADAPTER_REGISTRY.values():
        adapter.bootstrap(home)

    os.chdir(work_dir)
    log.info("cd %s; exec %s", work_dir, argv)
    os.execvp(argv[0], argv)
    return None


if __name__ == "__main__":
    sys.exit(main())
