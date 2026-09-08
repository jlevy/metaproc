"""Golden tests for the Steps section in ``metaproc status``.

Covers all five ``StepState`` values across a small DAG with a fan-out step,
in both text and JSON renderings. Uses crafted ``RunStatus`` fixtures so the
output is deterministic — no real run dispatch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from metaproc.commands.status import (
    _format_json,
    _format_steps_section,
    _format_text,
)
from metaproc.engine.run_status import RunStatus, StepStatusEntry
from metaproc.models.authored import ProgressCounts
from metaproc.models.runtime import StepState


def _all_states_run_status() -> RunStatus:
    return RunStatus(
        run_dir=Path("/tmp/golden-run"),
        started_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC),
        is_active=True,
        totals=ProgressCounts(
            total=0, pending=0, running=0, completed=0, failed=0, cached=0, retrying=0
        ),
        process_state="stale",
        steps=[
            StepStatusEntry(
                step_id="scaffold-roster",
                state=StepState.current,
                recorded_hash="a3f2cafe11110001",
                current_hash="a3f2cafe11110001",
            ),
            StepStatusEntry(
                step_id="business-setup",
                state=StepState.current,
                recorded_hash="b1010001beef0001",
                current_hash="b1010001beef0001",
                item_counts={"completed": 15, "total": 15},
            ),
            StepStatusEntry(
                step_id="edge-candidate-ledger",
                state=StepState.stale,
                recorded_hash="d77eaaaaaaaaaaaa",
                current_hash="e801bbbbbbbbbbbb",
                reason="definition changed (was d77eaaaaaaaaaaaa, now e801bbbbbbbbbbbb)",
            ),
            StepStatusEntry(
                step_id="edge-scenario-analysis",
                state=StepState.invalidated,
                recorded_hash="e9aacccccccccccc",
                current_hash="e9aacccccccccccc",
                reason="will rerun: marked .stale by --force or fingerprint cascade",
            ),
            StepStatusEntry(
                step_id="edge-assessment",
                state=StepState.in_flight,
                reason="currently running",
            ),
            StepStatusEntry(
                step_id="run-stats",
                state=StepState.missing,
            ),
        ],
    )


# ── Golden snapshots ─────────────────────────────────────────────


_EXPECTED_STEPS_TABLE = """\
Steps:
  step_id                 state                recorded →          current  items / reason
  ─────────────────────────────────────────────────────────────────────────────────────
  scaffold-roster         current      a3f2cafe11110001 → a3f2cafe11110001
  business-setup          current      b1010001beef0001 → b1010001beef0001  15/15 items
  edge-candidate-ledger   stale        d77eaaaaaaaaaaaa → e801bbbbbbbbbbbb  \
definition changed (was d77eaaaaaaaaaaaa, now e801bbbbbbbbbbbb)
  edge-scenario-analysis  invalidated  e9aacccccccccccc → e9aacccccccccccc  \
will rerun: marked .stale by --force or fingerprint cascade
  edge-assessment         in_flight                ---- →             ----  currently running
  run-stats               missing                  ---- →             ----\
"""


_EXPECTED_STEPS_TABLE_STALE_ONLY = """\
Steps:
  step_id                 state                recorded →          current  items / reason
  ─────────────────────────────────────────────────────────────────────────────────────
  edge-candidate-ledger   stale        d77eaaaaaaaaaaaa → e801bbbbbbbbbbbb  \
definition changed (was d77eaaaaaaaaaaaa, now e801bbbbbbbbbbbb)
  edge-scenario-analysis  invalidated  e9aacccccccccccc → e9aacccccccccccc  \
will rerun: marked .stale by --force or fingerprint cascade
  edge-assessment         in_flight                ---- →             ----  currently running\
"""


def test_steps_section_text_golden() -> None:
    rs = _all_states_run_status()
    assert _format_steps_section(rs) == _EXPECTED_STEPS_TABLE


def test_steps_section_text_golden_stale_only() -> None:
    rs = _all_states_run_status()
    assert _format_steps_section(rs, stale_only=True) == _EXPECTED_STEPS_TABLE_STALE_ONLY


def test_format_text_includes_process_summary_and_steps_table() -> None:
    rs = _all_states_run_status()
    rendered = _format_text(rs)
    # The fixture has 1 stale + 1 invalidated step → 2 steps need rerun.
    # in_flight is not counted: the orchestrator is already on it.
    assert "Process: stale (2 steps need rerun)" in rendered
    assert "edge-candidate-ledger" in rendered
    # The five-state vocabulary is all present.
    for state in ("current", "stale", "invalidated", "in_flight", "missing"):
        assert state in rendered, f"expected state label {state!r} in output"


def test_format_text_steps_only_suppresses_variant_block() -> None:
    rs = _all_states_run_status()
    rendered = _format_text(rs, steps_only=True)
    assert "Variant" not in rendered
    assert rendered.startswith("Steps:")


def test_format_text_process_current_when_no_stale_steps() -> None:
    rs = RunStatus(
        run_dir=Path("/tmp/golden-run"),
        is_active=False,
        totals=ProgressCounts(
            total=0, pending=0, running=0, completed=0, failed=0, cached=0, retrying=0
        ),
        process_state="current",
        steps=[
            StepStatusEntry(
                step_id="only-step",
                state=StepState.current,
                recorded_hash="a000000000000001",
                current_hash="a000000000000001",
            )
        ],
    )
    rendered = _format_text(rs)
    assert "Process: current" in rendered


def test_format_text_no_process_summary_when_plan_unavailable() -> None:
    rs = RunStatus(
        run_dir=Path("/tmp/golden-run"),
        is_active=False,
        totals=ProgressCounts(
            total=0, pending=0, running=0, completed=0, failed=0, cached=0, retrying=0
        ),
        process_state=None,
        steps=[],
    )
    rendered = _format_text(rs)
    assert "Process:" not in rendered
    assert "Steps:" not in rendered


def test_format_json_includes_steps_array_and_process_state() -> None:
    rs = _all_states_run_status()
    payload = _format_json(rs)
    assert payload["process_state"] == "stale"
    steps = cast("list[dict[str, object]]", payload["steps"])
    step_ids = [s["step_id"] for s in steps]
    assert step_ids == [
        "scaffold-roster",
        "business-setup",
        "edge-candidate-ledger",
        "edge-scenario-analysis",
        "edge-assessment",
        "run-stats",
    ]
    # State strings serialize as their enum values, not as enum names.
    state_values = {s["state"] for s in steps}
    assert state_values == {"current", "stale", "invalidated", "in_flight", "missing"}
    # Item counts survive the JSON round-trip for fan-out entries.
    business_setup = next(s for s in steps if s["step_id"] == "business-setup")
    assert business_setup["item_counts"] == {"completed": 15, "total": 15}
    # JSON is round-trippable.
    encoded = json.dumps(payload, default=str)
    assert json.loads(encoded)["process_state"] == "stale"


# ── Terminal-state label (this regression) ────────────────────


def _bare_run_status(
    *,
    is_active: bool,
    items_running: bool = False,
    orchestrator_alive: bool = False,
    pending_retries: int = 0,
) -> RunStatus:
    return RunStatus(
        run_dir=Path("/tmp/golden-run"),
        started_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC),
        is_active=is_active,
        items_running=items_running,
        orchestrator_alive=orchestrator_alive,
        pending_retries=pending_retries,
        totals=ProgressCounts(
            total=2, pending=0, running=0, completed=2, failed=0, cached=0, retrying=0
        ),
    )


def test_status_label_running_when_items_in_flight() -> None:
    rs = _bare_run_status(is_active=True, items_running=True, orchestrator_alive=True)
    assert "Status: RUNNING" in _format_text(rs)


def test_status_label_waiting_when_orchestrator_alive_but_no_items() -> None:
    """The case this regression was filed for: visible rows are 100 percent
    but the orchestrator has queued the next step. Should not say COMPLETE.
    """
    rs = _bare_run_status(is_active=True, items_running=False, orchestrator_alive=True)
    assert "Status: WAITING" in _format_text(rs)


def test_status_label_complete_when_fully_terminal() -> None:
    rs = _bare_run_status(is_active=False)
    assert "Status: COMPLETE" in _format_text(rs)


def test_status_label_failed_includes_error_and_hides_definition_summary() -> None:
    rs = _bare_run_status(is_active=False).model_copy(
        update={
            "process_execution_state": "failed",
            "process_error": "intake: RuntimeError: source attestation mismatch",
            "process_state": "current",
        }
    )

    rendered = _format_text(rs)

    assert "Status: FAILED" in rendered
    assert "Failure: intake: RuntimeError: source attestation mismatch" in rendered
    assert "Process: current" not in rendered


def test_live_orchestrator_outranks_a_carried_terminal_projection() -> None:
    rs = _bare_run_status(is_active=True, orchestrator_alive=True).model_copy(
        update={
            "process_execution_state": "failed",
            "process_error": "prior attempt failed",
            "process_state": "current",
        }
    )

    rendered = _format_text(rs)

    assert "Status: WAITING" in rendered
    assert "Failure:" not in rendered
    assert "Process: current" in rendered


def test_status_label_falls_back_to_running_when_subflags_absent() -> None:
    """Legacy callers that build RunStatus without sub-flags don't regress.

    A reader on an older artifact may construct RunStatus with only
    is_active and the sub-flags defaulting to False; the label still
    reads RUNNING (not the misleading WAITING) in that case.
    """
    rs = _bare_run_status(is_active=True, items_running=False, orchestrator_alive=False)
    assert "Status: RUNNING" in _format_text(rs)
