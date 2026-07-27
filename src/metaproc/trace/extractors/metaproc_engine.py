"""Extractor: metaproc engine + runpool events.

Reads:

- ``<run-dir>/.logs/process-events.jsonl`` — DAG-level orchestrator
  events (``process_start``, ``level_start``, ``step_start``,
  ``step_complete``, ``step_fail``, ``level_complete``, etc.).
- ``<run-dir>/.logs/runpool/steps/<step>/events.jsonl`` — per-step runpool subprocess
  lifecycle (``pool_start``, ``process_start``, ``process_exit``,
  ``pressure_check``, ``concurrency_adjust``).
- ``<run-dir>/.state/tasks/<step>/<item>/result.yaml`` — per-item
  step state (``completed``/``failed``/``in_progress``, ``validated``).

Emits ``run``, ``level``, ``step``, ``item``, ``subprocess``, ``internal``
spans. Step status comes from the matching ``step_complete`` /
``step_fail`` event; item status from ``result.yaml`` state.

Plan reference: § Span kinds + § Identifier conventions.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ruamel.yaml import YAMLError

from metaproc import paths as paths_mod
from metaproc.io import (
    iter_artifact_paths,
    iter_jsonl_objects,
    logical_path,
    read_yaml_file,
    resolve_existing_artifact,
)
from metaproc.trace.ids import compute_span_id
from metaproc.trace.schema import TraceEvent


class MetaprocEngineExtractor:
    """V1 extractor for metaproc engine + runpool event streams."""

    source = "metaproc-engine"

    def detect(self, run_dir: Path) -> bool:
        if paths_mod.process_events_log(run_dir).is_file():
            return True
        if any(_iter_runpool_event_paths(run_dir)):
            return True
        return bool((run_dir / ".state" / "tasks").is_dir())

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        yield from self._extract_process_events(run_dir, trace_id=trace_id)
        yield from self._extract_runpool_events(run_dir, trace_id=trace_id)
        yield from self._extract_item_results(run_dir, trace_id=trace_id)

    def _extract_process_events(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        path = resolve_existing_artifact(paths_mod.process_events_log(run_dir))
        if not path.is_file():
            return
        events = list(_iter_jsonl(path))

        run_starts = [e for e in events if e.get("event") == "process_start"]
        for ev in run_starts:
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, "run", trace_id),
                name=ev.get("process") or trace_id,
                kind="run",
                source=self.source,
                ts_start=str(ev.get("ts") or ""),
                attributes={
                    "run.id": ev.get("run_id"),
                    "run.process": ev.get("process"),
                    "run.backend": ev.get("backend"),
                    "run.step_count": ev.get("step_count"),
                },
            )

        levels_seen: dict[int, dict[str, Any]] = {}
        for ev in events:
            event_type = ev.get("event")
            if event_type == "level_start":
                level = ev.get("level")
                if isinstance(level, int):
                    levels_seen[level] = {"start": ev}
            elif event_type == "level_complete":
                level = ev.get("level")
                if isinstance(level, int) and level in levels_seen:
                    levels_seen[level]["end"] = ev

        for level, payload in levels_seen.items():
            start = payload.get("start", {})
            end = payload.get("end")
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, "level", level),
                parent_span_id=compute_span_id(self.source, "run", trace_id),
                name=f"level-{level}",
                kind="level",
                source=self.source,
                ts_start=str(start.get("ts") or ""),
                ts_end=str(end.get("ts")) if end else None,
                duration_ms=float(end["elapsed_s"]) * 1000.0
                if end and isinstance(end.get("elapsed_s"), (int, float))
                else None,
                status="ok" if end else "unknown",
                attributes={
                    "level.index": level,
                    "level.steps": start.get("steps"),
                },
            )

        steps_seen: dict[str, dict[str, Any]] = {}
        for ev in events:
            event_type = ev.get("event")
            step_id = ev.get("step_id")
            if not isinstance(step_id, str):
                continue
            if event_type == "step_start":
                steps_seen.setdefault(step_id, {})["start"] = ev
            elif event_type in ("step_complete", "step_fail"):
                steps_seen.setdefault(step_id, {})["end"] = ev

        for step_id, payload in steps_seen.items():
            start = payload.get("start", {})
            end = payload.get("end")
            if end is None:
                status: str = "unknown"
                error_payload: dict[str, Any] | None = None
            elif end.get("event") == "step_complete":
                status = "ok"
                error_payload = None
            else:
                status = "error"
                error_payload = {
                    "kind": "step_fail",
                    "message": end.get("error") or "",
                }
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, "step", step_id),
                name=step_id,
                kind="step",
                source=self.source,
                ts_start=str(start.get("ts") or ""),
                ts_end=str(end.get("ts")) if end else None,
                duration_ms=float(end["elapsed_s"]) * 1000.0
                if end and isinstance(end.get("elapsed_s"), (int, float))
                else None,
                status=status,  # type: ignore[arg-type]
                error=error_payload,
                attributes={
                    "step.id": step_id,
                    "step.mode": start.get("mode"),
                },
            )

    def _extract_runpool_events(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        for path in _iter_runpool_event_paths(run_dir):
            step_id = _step_from_runpool_path(path, run_dir)
            step_parent = compute_span_id(self.source, "step", step_id) if step_id else None
            yield from self._extract_one_runpool(
                path,
                trace_id=trace_id,
                step_id=step_id or "unknown",
                step_parent=step_parent,
            )

    def _extract_one_runpool(
        self,
        path: Path,
        *,
        trace_id: str,
        step_id: str,
        step_parent: str | None,
    ) -> Iterable[TraceEvent]:
        events = list(_iter_jsonl(path))
        process_starts: dict[int, dict[str, Any]] = {}
        for ev in events:
            event_type = ev.get("event")
            ts = ev.get("ts")
            if not isinstance(ts, str):
                continue
            if event_type == "process_start":
                pid = ev.get("pid")
                if isinstance(pid, int):
                    process_starts[pid] = ev
            elif event_type == "process_exit":
                pid = ev.get("pid")
                if not isinstance(pid, int):
                    continue
                start = process_starts.pop(pid, {})
                exit_code = ev.get("exit_code")
                status: str = "ok" if exit_code == 0 else "error"
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=compute_span_id(self.source, "subprocess", str(path), pid, ts),
                    parent_span_id=step_parent,
                    name=str(start.get("label") or "subprocess"),
                    kind="subprocess",
                    source=self.source,
                    ts_start=str(start.get("ts") or ts),
                    ts_end=ts,
                    duration_ms=float(ev["elapsed_s"]) * 1000.0
                    if isinstance(ev.get("elapsed_s"), (int, float))
                    else None,
                    status=status,  # type: ignore[arg-type]
                    error=None
                    if status == "ok"
                    else {"kind": "subprocess_failure", "exit_code": exit_code},
                    attributes={
                        "step.id": step_id,
                        "subprocess.pid": pid,
                        "subprocess.label": start.get("label"),
                        "subprocess.backend": start.get("backend"),
                        "subprocess.exit_code": exit_code,
                        "subprocess.peak_rss_bytes": ev.get("peak_rss_bytes"),
                    },
                )
            elif event_type in ("pressure_check", "concurrency_adjust"):
                yield TraceEvent(
                    trace_id=trace_id,
                    span_id=compute_span_id(self.source, "internal", str(path), event_type, ts),
                    parent_span_id=step_parent,
                    name=str(event_type),
                    kind="internal",
                    source=self.source,
                    ts_start=ts,
                    status="partial" if event_type == "pressure_check" else "ok",
                    attributes={
                        "step.id": step_id,
                        "internal.kind": event_type,
                        "internal.level": ev.get("level"),
                        "internal.available_pct": ev.get("available_pct"),
                    },
                )

    def _extract_item_results(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        state_root = run_dir / ".state" / "tasks"
        if not state_root.is_dir():
            return
        for result_yaml in sorted(state_root.rglob("result.yaml")):
            try:
                payload = read_yaml_file(result_yaml)
            except (YAMLError, OSError):
                continue
            if not isinstance(payload, dict):
                continue
            step_id = payload.get("step_id")
            try:
                rel = result_yaml.relative_to(state_root)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) < 2:
                continue
            item_key = parts[1]
            state = (payload.get("state") or "").lower()
            status: str
            if state == "completed":
                status = "ok"
            elif state == "failed":
                status = "error"
            elif state == "in_progress":
                status = "unknown"
            else:
                status = "unknown"
            yield TraceEvent(
                trace_id=trace_id,
                span_id=compute_span_id(self.source, "item", step_id or "unknown", item_key),
                parent_span_id=compute_span_id(self.source, "step", step_id or "unknown"),
                name=f"{step_id}/{item_key}",
                kind="item",
                source=self.source,
                ts_start=str(payload.get("published_at") or ""),
                ts_end=str(payload.get("published_at") or ""),
                status=status,  # type: ignore[arg-type]
                attributes={
                    "step.id": step_id,
                    "item.key": item_key,
                    "item.state": state,
                    "item.validated": payload.get("validated"),
                },
            )


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    yield from iter_jsonl_objects(path)


def _step_from_runpool_path(path: Path, run_dir: Path) -> str | None:
    """Return the step id from V2 or legacy step-scoped runpool event paths.

    Supported paths are ``.logs/runpool/steps/<step>/events.jsonl`` and
    ``.logs/steps/<step>/runpool-events.jsonl``.
    """
    path = logical_path(path)
    try:
        rel = path.relative_to(run_dir)
    except ValueError:
        rel = path
    parts = rel.parts
    try:
        idx = parts.index("steps")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _iter_runpool_event_paths(run_dir: Path) -> Iterable[Path]:
    """Yield runpool event streams from V2 paths, then exact legacy fallbacks."""
    paths: list[Path] = []

    v2_root = paths_mod.runpool_events(run_dir)
    v2_root = resolve_existing_artifact(v2_root)
    if v2_root.is_file():
        paths.append(v2_root)
    v2_steps_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
    if v2_steps_root.is_dir():
        paths.extend(iter_artifact_paths(v2_steps_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))
    v2_workers_root = paths_mod.runpool_logs_dir(run_dir) / paths_mod.WORKERS_SUBDIR
    if v2_workers_root.is_dir():
        paths.extend(iter_artifact_paths(v2_workers_root, f"*/{paths_mod.RUNPOOL_EVENTS_FILE}"))

    if not paths_mod.is_v2_run_layout(run_dir):
        legacy_root = paths_mod.legacy_runpool_events(run_dir)
        legacy_root = resolve_existing_artifact(legacy_root)
        if legacy_root.is_file():
            paths.append(legacy_root)
        legacy_steps_root = paths_mod.run_logs_dir(run_dir) / paths_mod.STEPS_SUBDIR
        if legacy_steps_root.is_dir():
            paths.extend(iter_artifact_paths(legacy_steps_root, f"*/{paths_mod.POOL_EVENTS_FILE}"))
        legacy_worker_paths = iter_artifact_paths(
            paths_mod.run_logs_dir(run_dir), f"worker-*/{paths_mod.POOL_EVENTS_FILE}"
        )
        paths.extend(p for p in legacy_worker_paths if p.is_file())

    seen: set[Path] = set()
    for candidate in paths:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        yield candidate
