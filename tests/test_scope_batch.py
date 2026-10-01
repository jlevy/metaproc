"""A bounded mapped-scope batch keeps one parent run and exact child selection."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.engine.fan_in import build_outcome_manifest
from metaproc.engine.resource_finalization import finalize_run_resources
from metaproc.engine.run_status import scan_run_status
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_status_at, reconcile_stale_running, write_status_at
from metaproc.models.runtime import StatusRecord


def test_batched_scoped_descendants_touch_only_named_mapped_items(
    tmp_path: Path, monkeypatch: Any
) -> None:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "roster.md").write_text(
        "---\nprogress:\n  items:\n    - ticker: AFL\n    - ticker: BBB\n    - ticker: CCC\n---\n",
        encoding="utf-8",
    )
    (process_dir / "ticker.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-ticker
              outputs:
                final: { path: "{{run.dir}}/final.txt", as: path }
              steps:
                - id: plan
                  mode: code
                  outputs:
                    query: { path: "{{run.dir}}/query.txt", kind: file }
                  command: /bin/sh -c 'mkdir -p "{{run.dir}}/../../barrier"; touch "{{run.dir}}/../../barrier/{{ticker}}"; i=0; until test -f "{{run.dir}}/../../barrier/AFL" && test -f "{{run.dir}}/../../barrier/BBB"; do i=$((i+1)); test "$i" -lt 80 || exit 23; sleep 0.05; done; printf x >> "{{run.dir}}/paid-surrogate-calls.txt"; printf query > "{{run.dir}}/query.txt"'
                - id: fetch
                  mode: code
                  needs: [plan]
                  outputs:
                    final: { path: "{{run.dir}}/final.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/provider-surrogate-calls.txt"; printf final > "{{run.dir}}/final.txt"'
            ---
            Provider-free selected child surrogate.
            """
        ),
        encoding="utf-8",
    )
    (process_dir / "parent.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-parent
              deps:
                roster: { path: ./roster.md, as: path }
                ticker: { path: ./ticker.process.md, as: path }
              outputs:
                final: { path: "{{run.dir}}/depth/AFL/final.txt", as: path }
              steps:
                - id: depth
                  mode: composite
                  uses: deps.ticker
                  for_each:
                    over: deps.roster
                    bind: ticker
                    bind_fields: [ticker]
                    key: "{{ticker}}"
                  outputs:
                    final: { path: "{{run.dir}}/depth/{{ticker}}/final.txt", kind: file }
            ---
            Provider-free mapped parent.
            """
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "batch"
    args = [
        "run-process",
        str(process_dir / "parent.process.md"),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        "RUN_ID=batch",
    ]
    pending_ccc = run_dir / ".state/tasks/depth/CCC"
    pending_status = write_status_at(
        pending_ccc,
        StatusRecord(
            run_id="synthetic-parent/batch",
            step_id="depth",
            item={"ticker": "CCC"},
            state="pending",
        ),
    )
    pending_bytes = pending_status.read_bytes()
    runner = CliRunner()
    finalizations: list[Path] = []
    real_finalize = finalize_run_resources

    def count_finalization(run_path: Path, **kwargs: Any) -> Any:
        finalizations.append(run_path)
        return real_finalize(run_path, **kwargs)

    monkeypatch.setattr("metaproc.commands.run_process.finalize_run_resources", count_finalization)
    selected = runner.invoke(
        app,
        [
            *args,
            "--only",
            "depth",
            "--max-concurrency",
            "2",
            "--initial-concurrency",
            "2",
            "--only-scope-step",
            "depth/AFL/plan",
            "--only-scope-step",
            "depth/BBB/plan",
        ],
    )
    assert selected.exit_code == 0, selected.output
    assert finalizations == [run_dir]
    for ticker in ("AFL", "BBB"):
        child = run_dir / "depth" / ticker
        assert (child / "paid-surrogate-calls.txt").read_text() == "x"
        assert (child / "provider-surrogate-calls.txt").read_text() == "x"
        status = read_status_at(run_dir / ".state/tasks/depth" / ticker)
        assert status is not None and status.state == "deferred"
    assert not (run_dir / "depth/CCC").exists()
    assert pending_status.read_bytes() == pending_bytes
    root_status = read_yaml_file(run_dir / ".state/process-status.yaml")
    assert root_status["selected_scope_step"].startswith("depth/batch-")
    assert root_status["selected_scope_steps"] == ["depth/AFL/plan", "depth/BBB/plan"]
    assert scan_run_status(run_dir).selected_scope_step is not None
    reconcile_stale_running(run_dir)
    outcomes = build_outcome_manifest(run_dir, "depth", ["AFL", "BBB", "CCC"]).payload[
        "fan_in_outcomes"
    ]
    assert outcomes["succeeded"] == 0
    assert {item["key"]: item["state"] for item in outcomes["items"]} == {
        "AFL": "deferred",
        "BBB": "deferred",
        "CCC": "pending",
    }
    ordinary = runner.invoke(app, args)
    assert ordinary.exit_code == 0, ordinary.output
    assert (run_dir / "depth/CCC/final.txt").read_text() == "final"
    for ticker in ("AFL", "BBB"):
        child = run_dir / "depth" / ticker
        assert (child / "paid-surrogate-calls.txt").read_text() == "x"
        assert (child / "provider-surrogate-calls.txt").read_text() == "x"
    complete_status = read_yaml_file(run_dir / ".state/process-status.yaml")
    assert "selected_scope_step" not in complete_status
    assert "selected_scope_steps" not in complete_status

    for paths, reason in (
        (("depth/AFL/plan", "depth/AFL/plan"), "distinct mapped items"),
        (("depth/AFL/plan", "depth/BBB/fetch"), "same descendant suffix"),
        (("depth/DDD/plan", "depth/AFL/plan"), "absent, duplicate, or terminal"),
    ):
        rejected = runner.invoke(
            app,
            [
                *args,
                "--only",
                "depth",
                *(part for path in paths for part in ("--only-scope-step", path)),
            ],
        )
        assert rejected.exit_code != 0
        assert reason in (rejected.output + str(rejected.exception))
        assert not (run_dir / "depth/DDD").exists()
        assert (run_dir / "depth/AFL/paid-surrogate-calls.txt").read_text() == "x"
        assert (run_dir / "depth/BBB/paid-surrogate-calls.txt").read_text() == "x"


def test_failed_batch_item_can_resume_without_repeating_successful_paid_work(
    tmp_path: Path,
) -> None:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "roster.md").write_text(
        "---\nprogress:\n  items:\n    - ticker: AFL\n    - ticker: BBB\n---\n",
        encoding="utf-8",
    )
    (process_dir / "ticker.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-ticker
              outputs:
                final: { path: "{{run.dir}}/final.txt", as: path }
              steps:
                - id: plan
                  mode: code
                  outputs:
                    query: { path: "{{run.dir}}/query.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/paid-surrogate-calls.txt"; printf query > "{{run.dir}}/query.txt"'
                - id: fetch
                  mode: code
                  needs: [plan]
                  outputs:
                    final: { path: "{{run.dir}}/final.txt", kind: file }
                  command: /bin/sh -c 'if [ "{{ticker}}" = BBB ] && [ ! -e "{{run.dir}}/failed.once" ]; then touch "{{run.dir}}/failed.once"; exit 7; fi; printf x >> "{{run.dir}}/provider-surrogate-calls.txt"; printf final > "{{run.dir}}/final.txt"'
            ---
            One selected provider surrogate fails once.
            """
        ),
        encoding="utf-8",
    )
    (process_dir / "parent.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-parent
              deps:
                roster: { path: ./roster.md, as: path }
                ticker: { path: ./ticker.process.md, as: path }
              steps:
                - id: depth
                  mode: composite
                  uses: deps.ticker
                  for_each:
                    over: deps.roster
                    bind: ticker
                    bind_fields: [ticker]
                    key: "{{ticker}}"
                  outputs:
                    final: { path: "{{run.dir}}/depth/{{ticker}}/final.txt", kind: file }
            ---
            Fail-once mapped parent.
            """
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "failed-batch"
    args = [
        "run-process",
        str(process_dir / "parent.process.md"),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        "RUN_ID=failed-batch",
    ]
    runner = CliRunner()
    partial = runner.invoke(
        app,
        [
            *args,
            "--only",
            "depth",
            "--only-scope-step",
            "depth/AFL/plan",
            "--only-scope-step",
            "depth/BBB/plan",
        ],
    )
    assert partial.exit_code != 0
    assert (run_dir / "depth/AFL/final.txt").read_text() == "final"
    assert not (run_dir / "depth/BBB/final.txt").exists()
    for ticker in ("AFL", "BBB"):
        assert (run_dir / f"depth/{ticker}/paid-surrogate-calls.txt").read_text() == "x"
    afl_status = read_status_at(run_dir / ".state/tasks/depth/AFL")
    assert afl_status is not None and afl_status.state == "deferred"
    bbb_status = read_status_at(run_dir / ".state/tasks/depth/BBB")
    assert bbb_status is not None and bbb_status.state == "failed"
    root_status = read_yaml_file(run_dir / ".state/process-status.yaml")
    assert root_status["selected_scope_step"].startswith("depth/batch-")
    reconcile_stale_running(run_dir)
    afl_status = read_status_at(run_dir / ".state/tasks/depth/AFL")
    assert afl_status is not None and afl_status.state == "deferred"
    bbb_status = read_status_at(run_dir / ".state/tasks/depth/BBB")
    assert bbb_status is not None and bbb_status.state == "failed"

    retry_failed_item = runner.invoke(
        app,
        [*args, "--only", "depth", "--only-scope-step", "depth/BBB/fetch"],
    )
    assert retry_failed_item.exit_code == 0, retry_failed_item.output
    assert (run_dir / "depth/BBB/final.txt").read_text() == "final"
    assert (run_dir / "depth/AFL/paid-surrogate-calls.txt").read_text() == "x"
    assert (run_dir / "depth/BBB/paid-surrogate-calls.txt").read_text() == "x"
    ordinary = runner.invoke(app, args)
    assert ordinary.exit_code == 0, ordinary.output
    complete_status = read_yaml_file(run_dir / ".state/process-status.yaml")
    assert "selected_scope_step" not in complete_status
    assert "selected_scope_steps" not in complete_status
    for ticker in ("AFL", "BBB"):
        assert (run_dir / f"depth/{ticker}/paid-surrogate-calls.txt").read_text() == "x"
        assert (run_dir / f"depth/{ticker}/provider-surrogate-calls.txt").read_text() == "x"
