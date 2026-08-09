"""Runtime CPU and RSS sampling for one executing step or task."""

from __future__ import annotations

import subprocess
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from metaproc.logutil.resource_events import ResourceEventLogger
from metaproc.models.resources import HierarchyRef, SourceRef
from metaproc.osutils.psutil_sampler import PsutilSampler
from metaproc.paths import LOGS_DIR, RESOURCE_EVENTS_FILE, run_config_file


@dataclass(frozen=True)
class _SamplingTarget:
    run_dir: Path
    run_id: str
    step_node_id: str


def _sampling_target(run_dir: Path, run_id: str, step_node_id: str) -> _SamplingTarget:
    """Resolve composite children back to the root resource ledger hierarchy."""
    root_run_dir = next(
        (
            candidate
            for candidate in (run_dir, *run_dir.parents)
            if run_config_file(candidate).is_file()
        ),
        run_dir,
    )
    subgraph_parts = run_dir.relative_to(root_run_dir).parts
    if not subgraph_parts:
        return _SamplingTarget(run_dir=run_dir, run_id=run_id, step_node_id=step_node_id)

    nested_run_suffix = "/" + "/".join(subgraph_parts)
    root_run_id = run_id.removesuffix(nested_run_suffix)
    return _SamplingTarget(
        run_dir=root_run_dir,
        run_id=root_run_id,
        step_node_id="::".join((*subgraph_parts, step_node_id)),
    )


@contextmanager
def sample_step_resources(
    *,
    run_dir: Path,
    run_id: str,
    step_node_id: str,
    item_key: str | None = None,
    pid: int | None = None,
) -> Generator[PsutilSampler, None, None]:
    """Persist psutil samples under the deepest known step hierarchy."""
    target = _sampling_target(run_dir, run_id, step_node_id)
    event_path = target.run_dir / LOGS_DIR / RESOURCE_EVENTS_FILE
    source = SourceRef(
        kind="psutil_sampler",
        path=event_path.relative_to(target.run_dir).as_posix(),
    )
    hierarchy = HierarchyRef(
        run_id=target.run_id,
        step_node_id=target.step_node_id,
        item_key=item_key,
    )
    with (
        ResourceEventLogger(event_path) as logger,
        PsutilSampler(
            hierarchy=hierarchy,
            source=source,
            logger=logger,
            pid=pid,
        ) as sampler,
    ):
        yield sampler


def run_sampled_step_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    run_dir: Path,
    run_id: str,
    step_node_id: str,
    item_key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a code-step command while sampling only its process tree."""
    args = list(command)
    with subprocess.Popen(
        args,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        with sample_step_resources(
            run_dir=run_dir,
            run_id=run_id,
            step_node_id=step_node_id,
            item_key=item_key,
            pid=process.pid,
        ):
            stdout, stderr = process.communicate()

    completed = subprocess.CompletedProcess(
        args=args,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    completed.check_returncode()
    return completed
