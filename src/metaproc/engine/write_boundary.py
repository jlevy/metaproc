"""Write-boundary helpers for agent steps.

Phase 6 formalizes two rules:
1. Fan-out agent steps cannot write directly into shared mutable trees outside
   ``{{run.dir}}``.
2. Agent-owned write surfaces must not overlap.

At runtime we also reject repo writes outside the declared write surface by
comparing the git worktree state before and after an agent launch.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from metaproc.engine.placeholders import resolve_templates
from metaproc.models.authored import IOSpec, ProcessSpec, ProcessStep
from metaproc.paths import is_path_under


@dataclass(frozen=True)
class WriteTarget:
    """A declared writable location."""

    path: Path
    kind: Literal["exact", "tree"]


@dataclass(frozen=True)
class RepoSnapshot:
    """Git worktree snapshot used for write-boundary checks."""

    repo_root: Path
    statuses: dict[str, str]
    dirty_stats: dict[str, tuple[bool, int, int] | None]


@dataclass(frozen=True)
class WriteBoundaryOverlap:
    """Two agent steps declare writes into an overlapping tree."""

    left_step: str
    right_step: str
    left_path: Path
    right_path: Path

    @property
    def message(self) -> str:
        return (
            f"agent steps '{self.left_step}' and '{self.right_step}' declare overlapping "
            f"write boundaries ({self.left_path} vs {self.right_path})"
        )

    @property
    def suggested_fix(self) -> str:
        return (
            "Move the overlapping output out of the source tree into the per-run "
            "artifact tree (e.g. {{run.dir}}/<step>/...) so the two agents cannot "
            "write into the same directory."
        )


class WriteBoundaryOverlapError(ValueError):
    """Process spec declares overlapping agent write boundaries.

    Subclass of ValueError (callers that catch ValueError still work). Carries
    structured ``overlaps`` so typed consumers (browser, CLI) can surface step
    IDs, declared paths, and a suggested fix instead of treating the failure as
    a soft warning.
    """

    def __init__(self, overlaps: list[WriteBoundaryOverlap]):
        self.overlaps: list[WriteBoundaryOverlap] = list(overlaps)
        body = "\n".join(f"  - {overlap.message}" for overlap in self.overlaps)
        fix = self.overlaps[0].suggested_fix if self.overlaps else ""
        super().__init__(
            "invalid agent write boundaries:\n" + body + (f"\nSuggested fix: {fix}" if fix else "")
        )


def validate_agent_write_boundaries(
    spec: ProcessSpec,
    params: dict[str, str],
    *,
    step_outputs: dict[str, dict[str, IOSpec]],
) -> list[str]:
    """Return plan-build errors for invalid agent write ownership."""
    run_dir_text = resolve_templates("{{run.dir}}", params)
    run_dir = Path(run_dir_text)
    run_dir_resolved = "{{" not in run_dir_text and "}}" not in run_dir_text
    errors: list[str] = []
    targets_by_step: dict[str, list[WriteTarget]] = {}

    for step in spec.steps:
        if step.mode != "agent":
            continue
        resolved_outputs = step_outputs.get(step.id, {})
        targets = collect_write_targets(
            step,
            resolved_outputs,
            params,
            step_scope=False,
        )
        if not targets:
            errors.append(
                f"step '{step.id}': agent steps must declare outputs or output_root for "
                "write-boundary enforcement"
            )
            continue

        if step.for_each is not None and run_dir_resolved:
            for target in targets:
                if not is_path_under(run_dir, target.path):
                    errors.append(
                        f"step '{step.id}': fan-out agent outputs must stay under "
                        f"{{{{run.dir}}}}; got {target.path}"
                    )
        targets_by_step[step.id] = targets

    for overlap in _find_overlaps(targets_by_step):
        errors.append(overlap.message)

    return errors


def find_write_boundary_overlaps(
    spec: ProcessSpec,
    params: dict[str, str],
    *,
    step_outputs: dict[str, dict[str, IOSpec]],
) -> list[WriteBoundaryOverlap]:
    """Return structured overlap records for agent-step pairs with overlapping writes.

    Companion to ``validate_agent_write_boundaries``: same detection logic, but
    preserves step IDs and declared paths for typed error reporting. Callers that
    need to refuse to launch on overlap (run-process, browser) should raise
    ``WriteBoundaryOverlapError`` with this list.
    """
    targets_by_step: dict[str, list[WriteTarget]] = {}
    for step in spec.steps:
        if step.mode != "agent":
            continue
        resolved_outputs = step_outputs.get(step.id, {})
        targets = collect_write_targets(
            step,
            resolved_outputs,
            params,
            step_scope=False,
        )
        if targets:
            targets_by_step[step.id] = targets
    return _find_overlaps(targets_by_step)


def _find_overlaps(
    targets_by_step: dict[str, list[WriteTarget]],
) -> list[WriteBoundaryOverlap]:
    overlaps: list[WriteBoundaryOverlap] = []
    step_ids = sorted(targets_by_step)
    for idx, left_step in enumerate(step_ids):
        for right_step in step_ids[idx + 1 :]:
            left_targets = targets_by_step[left_step]
            right_targets = targets_by_step[right_step]
            overlap_pair = next(
                (
                    (left_target, right_target)
                    for left_target in left_targets
                    for right_target in right_targets
                    if targets_overlap(left_target, right_target)
                ),
                None,
            )
            if overlap_pair is None:
                continue
            left_target, right_target = overlap_pair
            overlaps.append(
                WriteBoundaryOverlap(
                    left_step=left_step,
                    right_step=right_step,
                    left_path=left_target.path,
                    right_path=right_target.path,
                )
            )
    return overlaps


def collect_write_targets(
    step: ProcessStep,
    outputs: dict[str, IOSpec],
    variables: dict[str, str],
    *,
    step_scope: bool,
) -> list[WriteTarget]:
    """Resolve a step's declared writable paths.

    ``step_scope=True`` broadens unresolved item-scoped paths into the stable
    parent prefix so a single snapshot can cover the whole fan-out step.
    ``step_scope=False`` keeps exact per-item paths where possible.
    """
    targets: list[WriteTarget] = []

    if step.output_root:
        root_text = resolve_templates(step.output_root, variables)
        root = _step_scope_path(root_text) if step_scope else Path(root_text)
        if root is not None:
            targets.append(WriteTarget(path=root, kind="tree"))

    if targets:
        return _dedupe_targets(targets)

    for output in outputs.values():
        if not output.path:
            continue
        path_text = resolve_templates(output.path, variables)
        if step_scope:
            scoped = _step_scope_path(path_text)
            if scoped is None:
                continue
            kind: Literal["exact", "tree"] = (
                "tree" if output.kind == "directory" or _has_unresolved(path_text) else "exact"
            )
            targets.append(WriteTarget(path=scoped, kind=kind))
            continue

        path = Path(path_text)
        kind = "tree" if output.kind == "directory" else "exact"
        targets.append(WriteTarget(path=path, kind=kind))

    return _dedupe_targets(targets)


def capture_repo_snapshot(
    anchor: Path,
    *,
    observation_zone: list[Path] | None = None,
) -> RepoSnapshot | None:
    """Capture git worktree state, or return ``None`` outside a git repo.

    ``observation_zone`` limits the snapshot to paths under one of the given
    roots. Concurrent writes outside the zone are ignored so the boundary
    check only attributes changes we could plausibly have caused. When
    ``None`` the whole repo is observed (legacy behavior).
    """
    repo_root = _find_repo_root(anchor)
    if repo_root is None:
        return None

    statuses = _git_status(repo_root)
    if observation_zone is not None:
        zone = [p.resolve() for p in observation_zone]
        statuses = {
            rel_path: code
            for rel_path, code in statuses.items()
            if _rel_path_in_zone(repo_root, rel_path, zone)
        }
    dirty_stats = {rel_path: _stat_tuple(repo_root / rel_path) for rel_path in statuses}
    return RepoSnapshot(repo_root=repo_root, statuses=statuses, dirty_stats=dirty_stats)


def _rel_path_in_zone(repo_root: Path, rel_path: str, zone: list[Path]) -> bool:
    abs_path = (repo_root / rel_path).resolve()
    return any(is_path_under(zone_root, abs_path) for zone_root in zone)


def repo_changes_since(
    before: RepoSnapshot | None,
    after: RepoSnapshot | None,
) -> list[Path]:
    """Return repo paths whose git-visible state changed."""
    if before is None or after is None:
        return []

    changed: list[Path] = []
    rel_paths = set(before.statuses) | set(after.statuses)
    for rel_path in sorted(rel_paths):
        before_status = before.statuses.get(rel_path)
        after_status = after.statuses.get(rel_path)
        before_stat = before.dirty_stats.get(rel_path)
        after_stat = after.dirty_stats.get(rel_path)
        if before_status != after_status or before_stat != after_stat:
            changed.append((after.repo_root / rel_path).resolve())
    return changed


def filter_boundary_violations(
    changed_paths: list[Path],
    *,
    allowed: list[WriteTarget],
    ignored: list[WriteTarget] | None = None,
    base_dir: Path | None = None,
) -> list[Path]:
    """Filter changed paths down to writes outside the allowed boundary.

    ``base_dir`` anchors relative ``WriteTarget`` paths so they compare on the
    same footing as ``changed_paths`` (which are absolute paths rooted at the
    repo). Without this anchor, a relative ``RUNS_DIR`` causes every
    legitimate write to be flagged as a violation because
    ``Path.relative_to`` rejects a relative-vs-absolute comparison.
    """
    allowed_targets = _resolve_targets_against(allowed, base_dir)
    ignored_targets = _resolve_targets_against(ignored or [], base_dir)
    violations: list[Path] = []
    for changed_path in changed_paths:
        if _path_matches_any(changed_path, ignored_targets):
            continue
        if _path_matches_any(changed_path, allowed_targets):
            continue
        violations.append(changed_path)
    return violations


def _resolve_targets_against(
    targets: list[WriteTarget],
    base_dir: Path | None,
) -> list[WriteTarget]:
    if base_dir is None:
        return targets
    base = base_dir.resolve()
    resolved: list[WriteTarget] = []
    for target in targets:
        if target.path.is_absolute():
            resolved.append(target)
            continue
        resolved.append(WriteTarget(path=(base / target.path).resolve(), kind=target.kind))
    return resolved


def targets_overlap(left: WriteTarget, right: WriteTarget) -> bool:
    """Return whether two declared write targets overlap."""
    if left.kind == "exact" and right.kind == "exact":
        return left.path == right.path
    if left.kind == "tree" and right.kind == "tree":
        return is_path_under(left.path, right.path) or is_path_under(right.path, left.path)
    if left.kind == "tree":
        return is_path_under(left.path, right.path)
    return is_path_under(right.path, left.path)


def _path_matches_any(path: Path, targets: list[WriteTarget]) -> bool:
    for target in targets:
        if target.kind == "exact":
            if path == target.path:
                return True
            continue
        if is_path_under(target.path, path):
            return True
    return False


def _dedupe_targets(targets: list[WriteTarget]) -> list[WriteTarget]:
    deduped: list[WriteTarget] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        key = (str(target.path), target.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _has_unresolved(path_text: str) -> bool:
    return "{{" in path_text and "}}" in path_text


def _step_scope_path(path_text: str) -> Path | None:
    """Trim unresolved suffix segments to the stable step-level prefix."""
    path = Path(path_text)
    if not _has_unresolved(path_text):
        return path

    stable_parts: list[str] = []
    for part in path.parts:
        if "{{" in part and "}}" in part:
            break
        stable_parts.append(part)
    if not stable_parts:
        return None
    return Path(*stable_parts)


def _find_repo_root(anchor: Path) -> Path | None:
    current = anchor.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_status(repo_root: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return {}

    statuses: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        statuses[raw_path] = code
    return statuses


def _stat_tuple(path: Path) -> tuple[bool, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (path.is_file(), stat.st_mtime_ns, stat.st_size)
