from __future__ import annotations

from pathlib import Path

from metaproc.commands.pulse import (
    PulseMetrics,
    _resolve_batch_run_dirs,
    format_pulse,
    gather_pulse,
)


def test_format_pulse_full_line() -> None:
    m = PulseMetrics(
        orch_state="alive",
        orch_pid=123,
        done=5,
        total=10,
        hb_age_s=4,
        ev_age_s=8,
        queue=2,
        recent_halts=0,
        auth_dist={"alt1": 3, "alt2": 2},
    )
    assert format_pulse(m) == (
        "PULSE: orch=alive pid=123 N=5/10 hb=4s ev_age=8s queue=2 recent_halts=0 auth=alt1:3|alt2:2"
    )


def test_format_pulse_missing_fields_and_no_auth() -> None:
    m = PulseMetrics(
        orch_state="dead",
        orch_pid=None,
        done=None,
        total=None,
        hb_age_s=None,
        ev_age_s=None,
        queue=0,
        recent_halts=0,
        auth_dist={},
    )
    assert format_pulse(m) == (
        "PULSE: orch=dead pid=? N=?/? hb=?s ev_age=?s queue=0 recent_halts=0"
    )


def test_gather_pulse_reads_run_dir_state(tmp_path: Path) -> None:
    run = tmp_path / "run"
    state = run / ".state"
    logs = run / ".logs"
    state.mkdir(parents=True)
    logs.mkdir(parents=True)
    (state / "orchestrator-lease.yaml").write_text(
        "owner_pid: 2147483646\nlast_heartbeat_at: '2026-05-24T00:00:00+00:00'\n"
    )
    (state / "runpool-status.yaml").write_text(
        "active_count: 2\ncompleted_count: 5\npending_count: 1\nfailed_count: 0\nkilled_count: 0\n"
    )
    (logs / "process-events.jsonl").write_text('{"event": "step_start"}\n')
    (logs / "runpool-events.jsonl").write_text(
        '{"event": "auth_lease_acquired", "label": "alt1"}\n'
        '{"event": "auth_lease_acquired", "label": "alt1"}\n'
        '{"event": "auth_lease_acquired", "label": "alt2"}\n'
        '{"event": "step_fail", "label": "alt1"}\n'
    )

    m = gather_pulse(run)
    assert m.done == 5
    assert m.queue == 2
    assert m.total == 8  # 5 + 2 + 1 + 0 + 0
    assert m.auth_dist == {"alt1": 2, "alt2": 1}
    assert m.recent_halts == 1
    # orch_pid 2147483646 is not a live process under test -> dead.
    assert m.orch_state == "dead"
    assert m.ev_age_s is not None  # process-events.jsonl exists


def test_gather_pulse_empty_run_dir(tmp_path: Path) -> None:
    m = gather_pulse(tmp_path)
    assert m.orch_state == "dead"
    assert m.done is None
    assert m.total is None
    assert m.queue == 0
    assert m.auth_dist == {}


# ── internal-reference: conc / ceil / pressure fields ────────────────


def test_format_pulse_with_concurrency_and_pressure() -> None:
    m = PulseMetrics(
        orch_state="alive",
        orch_pid=123,
        done=5,
        total=10,
        hb_age_s=4,
        ev_age_s=8,
        queue=2,
        recent_halts=0,
        auth_dist={},
        current_concurrency=6,
        max_concurrency=8,
        memory_ceiling=6,
        pressure_level="elevated",
        pressure_available_pct=18.5,
    )
    assert format_pulse(m) == (
        "PULSE: orch=alive pid=123 N=5/10 hb=4s ev_age=8s queue=2 recent_halts=0 "
        "conc=6/8 ceil=6 pressure=elevated@18%"
    )


def test_format_pulse_partial_conc_fields() -> None:
    """Only current_concurrency populated — still renders the segment with ? for max."""
    m = PulseMetrics(
        orch_state="alive",
        orch_pid=1,
        done=0,
        total=0,
        hb_age_s=0,
        ev_age_s=0,
        queue=0,
        recent_halts=0,
        auth_dist={},
        current_concurrency=3,
        max_concurrency=None,
        memory_ceiling=None,
        pressure_level=None,
    )
    rendered = format_pulse(m)
    assert "conc=3/?" in rendered
    assert "ceil=" not in rendered
    assert "pressure=" not in rendered


def test_format_pulse_no_new_fields_omits_segments() -> None:
    """A run without runpool-status.yaml omits the new segments entirely."""
    m = PulseMetrics(
        orch_state="dead",
        orch_pid=None,
        done=None,
        total=None,
        hb_age_s=None,
        ev_age_s=None,
        queue=0,
        recent_halts=0,
        auth_dist={},
    )
    rendered = format_pulse(m)
    # Backward-compatibility: existing parsers must still match this shape.
    assert rendered == "PULSE: orch=dead pid=? N=?/? hb=?s ev_age=?s queue=0 recent_halts=0"
    assert "conc=" not in rendered
    assert "ceil=" not in rendered
    assert "pressure=" not in rendered


def test_gather_pulse_reads_concurrency_and_pressure(tmp_path: Path) -> None:
    """internal-reference: runpool-status.yaml current_concurrency / controller.memory_ceiling /
    pressure fields are surfaced through to PulseMetrics."""
    run = tmp_path / "run"
    state = run / ".state"
    state.mkdir(parents=True)
    (state / "runpool-status.yaml").write_text(
        """
active_count: 3
completed_count: 5
pending_count: 4
failed_count: 0
killed_count: 0
current_concurrency: 6
max_concurrency: 8
controller:
  memory_ceiling: 6
  effective_target: 6
pressure:
  level: elevated
  available_pct: 18.5
"""
    )
    m = gather_pulse(run)
    assert m.current_concurrency == 6
    assert m.max_concurrency == 8
    assert m.memory_ceiling == 6
    assert m.pressure_level == "elevated"
    assert m.pressure_available_pct == 18.5


# ── internal-reference Part B: --batch glob/parent resolution ────────


def test_resolve_batch_finds_sibling_runs_under_parent(tmp_path: Path) -> None:
    """A parent directory resolves to all immediate child dirs that contain .state."""
    parent = tmp_path / "runs"
    parent.mkdir()
    for lane in ("lane-a", "lane-b", "lane-c"):
        (parent / lane / ".state").mkdir(parents=True)
    # A bare directory without .state is not a run dir; should be excluded.
    (parent / "not-a-run").mkdir()

    matches = _resolve_batch_run_dirs(parent)
    assert [p.name for p in matches] == ["lane-a", "lane-b", "lane-c"]


def test_resolve_batch_glob_pattern(tmp_path: Path, monkeypatch) -> None:
    """A glob pattern resolves to all matching directories."""
    parent = tmp_path / "runs"
    parent.mkdir()
    for lane in ("alpha-claude", "alpha-glm", "beta-claude"):
        (parent / lane / ".state").mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    matches = _resolve_batch_run_dirs(Path("runs/alpha-*"))
    assert sorted(p.name for p in matches) == ["alpha-claude", "alpha-glm"]


def test_resolve_batch_single_run_dir_falls_through(tmp_path: Path) -> None:
    """A single run dir (no sibling children with .state) returns a single-item list."""
    run = tmp_path / "single"
    (run / ".state").mkdir(parents=True)
    matches = _resolve_batch_run_dirs(run)
    # The run itself has .state at the leaf; iterating its children finds no
    # .state-bearing subdirs, so the resolver returns the run itself.
    assert matches == [run]


def test_resolve_batch_nonexistent_returns_empty(tmp_path: Path) -> None:
    matches = _resolve_batch_run_dirs(tmp_path / "does-not-exist")
    assert matches == []
