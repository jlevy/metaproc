"""A bounded nested through-target selection preserves paid work and stops before fetch."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.run_process import _write_process_status
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_status_at


@pytest.mark.parametrize("prior_selected_child", [False, True])
def test_through_target_reuses_prerequisite_composites_and_stops_before_fetch(
    tmp_path: Path,
    prior_selected_child: bool,
) -> None:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "roster.md").write_text(
        "---\nprogress:\n  items:\n    - ticker: AFL\n---\n", encoding="utf-8"
    )
    (process_dir / "prereq.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-prereq
              outputs:
                token: { path: "{{run.dir}}/token.txt", as: path }
              steps:
                - id: make-token
                  mode: code
                  outputs:
                    token: { path: "{{run.dir}}/token.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/prereq-calls.txt"; printf token > "{{run.dir}}/token.txt"'
            ---
            Paid prerequisite surrogate.
            """
        ),
        encoding="utf-8",
    )
    (process_dir / "query.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-query
              outputs:
                plan: { path: "{{run.dir}}/plan.txt", as: path }
              steps:
                - id: planner
                  mode: code
                  outputs:
                    draft: { path: "{{run.dir}}/draft.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/planner-calls.txt"; printf draft > "{{run.dir}}/draft.txt"'
                - id: publish
                  mode: code
                  needs: [planner]
                  outputs:
                    plan: { path: "{{run.dir}}/plan.txt", kind: file }
                  command: /bin/sh -c 'printf plan > "{{run.dir}}/plan.txt"'
            ---
            Paid planner surrogate.
            """
        ),
        encoding="utf-8",
    )
    (process_dir / "ticker.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-ticker
              deps:
                prereq: { path: ./prereq.process.md, as: path }
                query: { path: ./query.process.md, as: path }
              outputs:
                final: { path: "{{run.dir}}/final.txt", as: path }
              steps:
                - id: unrelated
                  mode: code
                  outputs:
                    unrelated: { path: "{{run.dir}}/unrelated.txt", kind: file }
                  command: /bin/sh -c 'printf unrelated > "{{run.dir}}/unrelated.txt"'
                - id: prereq
                  mode: composite
                  uses: deps.prereq
                  outputs:
                    token: { path: "{{run.dir}}/prereq/token.txt", kind: file }
                - id: stage
                  mode: code
                  needs: [prereq]
                  outputs:
                    staged: { path: "{{run.dir}}/staged.txt", kind: file }
                  command: /bin/sh -c 'printf staged > "{{run.dir}}/staged.txt"'
                - id: query
                  mode: composite
                  uses: deps.query
                  needs: [stage]
                  outputs:
                    plan: { path: "{{run.dir}}/query/plan.txt", kind: file }
                - id: fetch
                  mode: code
                  needs: [query]
                  outputs:
                    final: { path: "{{run.dir}}/final.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/provider-calls.txt"; printf final > "{{run.dir}}/final.txt"'
            ---
            One bounded depth item.
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
            One mapped parent.
            """
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    run = runs_dir / "through"
    args = [
        "run-process",
        str(process_dir / "parent.process.md"),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        "RUN_ID=through",
    ]
    bounded = [
        *args,
        "--only",
        "depth",
        "--only-scope-step",
        "depth/AFL/query",
        "--scope-through-target",
    ]
    runner = CliRunner()
    if prior_selected_child:
        staged = runner.invoke(
            app,
            [
                *args,
                "--only",
                "depth",
                "--only-scope-step",
                "depth/AFL/stage",
                "--scope-through-target",
            ],
        )
        assert staged.exit_code == 0, staged.output
        prior = runner.invoke(
            app,
            [*args, "--only", "depth", "--only-scope-step", "depth/AFL/query/planner"],
        )
        assert prior.exit_code == 0, prior.output
        prior_depth = read_yaml_file(run / "depth/AFL/.state/process-status.yaml")
        assert prior_depth["steps"]["query"]["state"] == "skipped"
        assert (run / "depth/AFL/query/planner-calls.txt").read_text() == "x"
    first = runner.invoke(app, bounded)
    assert first.exit_code == 0, first.output
    child = run / "depth/AFL"
    assert (child / "prereq/prereq-calls.txt").read_text() == "x"
    assert (child / "query/planner-calls.txt").read_text() == "x"
    assert (child / "query/plan.txt").read_text() == "plan"
    depth_status = read_yaml_file(child / ".state/process-status.yaml")
    assert depth_status["steps"]["query"]["state"] == "completed"
    assert not (child / "provider-calls.txt").exists()
    assert not (child / "unrelated.txt").exists()
    mapped = read_status_at(run / ".state/tasks/depth/AFL")
    assert mapped is not None and mapped.state == "deferred"
    first_status = read_yaml_file(run / ".state/process-status.yaml")
    assert first_status["selected_scope_step"] == "depth/AFL/query"
    assert first_status["selected_scope_mode"] == "through"

    second = runner.invoke(app, bounded)
    assert second.exit_code == 0, second.output
    assert (child / "prereq/prereq-calls.txt").read_text() == "x"
    assert (child / "query/planner-calls.txt").read_text() == "x"
    assert not (child / "provider-calls.txt").exists()

    selected_fetch = runner.invoke(
        app,
        [*args, "--only", "depth", "--only-scope-step", "depth/AFL/fetch"],
    )
    assert selected_fetch.exit_code == 0, selected_fetch.output
    assert (child / "provider-calls.txt").read_text() == "x"
    assert (child / "query/planner-calls.txt").read_text() == "x"

    ordinary = runner.invoke(app, args)
    assert ordinary.exit_code == 0, ordinary.output
    assert (child / "provider-calls.txt").read_text() == "x"
    assert (child / "prereq/prereq-calls.txt").read_text() == "x"
    assert (child / "query/planner-calls.txt").read_text() == "x"
    complete_status = read_yaml_file(run / ".state/process-status.yaml")
    assert "selected_scope_step" not in complete_status
    assert "selected_scope_mode" not in complete_status


@pytest.mark.parametrize("prior_selected_children", [False, True])
def test_batched_through_target_runs_only_named_planner_scopes(
    tmp_path: Path,
    prior_selected_children: bool,
) -> None:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "roster.md").write_text(
        "---\nprogress:\n  items:\n    - ticker: AFL\n    - ticker: BBB\n    - ticker: CCC\n---\n",
        encoding="utf-8",
    )
    (process_dir / "query.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-query
              outputs:
                plan: { path: "{{run.dir}}/plan.txt", as: path }
              steps:
                - id: planner
                  mode: code
                  outputs:
                    draft: { path: "{{run.dir}}/draft.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/planner-calls.txt"; printf draft > "{{run.dir}}/draft.txt"'
                - id: publish
                  mode: code
                  needs: [planner]
                  outputs:
                    plan: { path: "{{run.dir}}/plan.txt", kind: file }
                  command: /bin/sh -c 'if [ "{{ticker}}" = BBB ] && [ ! -e "{{run.dir}}/failed.once" ]; then touch "{{run.dir}}/failed.once"; exit 7; fi; printf plan > "{{run.dir}}/plan.txt"'
            ---
            Planner surrogate succeeds before one selected publish failure.
            """
        ),
        encoding="utf-8",
    )
    (process_dir / "ticker.process.md").write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: synthetic-ticker
              deps:
                query: { path: ./query.process.md, as: path }
              outputs:
                final: { path: "{{run.dir}}/final.txt", as: path }
              steps:
                - id: stage
                  mode: code
                  outputs:
                    staged: { path: "{{run.dir}}/staged.txt", kind: file }
                  command: /bin/sh -c 'printf staged > "{{run.dir}}/staged.txt"'
                - id: query
                  mode: composite
                  uses: deps.query
                  needs: [stage]
                  outputs:
                    plan: { path: "{{run.dir}}/query/plan.txt", kind: file }
                - id: fetch
                  mode: code
                  needs: [query]
                  outputs:
                    final: { path: "{{run.dir}}/final.txt", kind: file }
                  command: /bin/sh -c 'printf x >> "{{run.dir}}/provider-calls.txt"; printf final > "{{run.dir}}/final.txt"'
            ---
            One mapped depth item.
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
            One mapped parent.
            """
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    run = runs_dir / "batch-through"
    args = [
        "run-process",
        str(process_dir / "parent.process.md"),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        "RUN_ID=batch-through",
    ]
    bounded = [
        *args,
        "--only",
        "depth",
        "--scope-through-target",
        "--only-scope-step",
        "depth/AFL/query",
        "--only-scope-step",
        "depth/BBB/query",
    ]
    runner = CliRunner()
    if prior_selected_children:
        staged = runner.invoke(
            app,
            [
                *args,
                "--only",
                "depth",
                "--scope-through-target",
                "--only-scope-step",
                "depth/AFL/stage",
                "--only-scope-step",
                "depth/BBB/stage",
            ],
        )
        assert staged.exit_code == 0, staged.output
        nested = [
            *args,
            "--only",
            "depth",
            "--only-scope-step",
            "depth/AFL/query/planner",
            "--only-scope-step",
            "depth/BBB/query/planner",
        ]
        # Seed a successful historical selected-child state. A repeat of the
        # downstream planner selector would force the paid step by design.
        (run / "depth/BBB/query").mkdir(parents=True, exist_ok=True)
        (run / "depth/BBB/query/failed.once").touch()
        prepared = runner.invoke(app, nested)
        assert prepared.exit_code == 0, prepared.output
        for ticker in ("AFL", "BBB"):
            depth_status = read_yaml_file(run / "depth" / ticker / ".state/process-status.yaml")
            assert depth_status["steps"]["query"]["state"] == "skipped"
            assert (run / "depth" / ticker / "query/planner-calls.txt").read_text() == "x"
    else:
        first = runner.invoke(app, bounded)
        assert first.exit_code != 0
        assert (run / "depth/AFL/query/planner-calls.txt").read_text() == "x"
        assert (run / "depth/BBB/query/planner-calls.txt").read_text() == "x"
        assert not (run / "depth/BBB/query/plan.txt").exists()
        assert not (run / "depth/AFL/provider-calls.txt").exists()
    for _ in range(2):
        selected = runner.invoke(app, bounded)
        assert selected.exit_code == 0, selected.output
    for ticker in ("AFL", "BBB"):
        child = run / "depth" / ticker
        assert (child / "staged.txt").read_text() == "staged"
        assert (child / "query/planner-calls.txt").read_text() == "x"
        depth_status = read_yaml_file(child / ".state/process-status.yaml")
        assert depth_status["steps"]["query"]["state"] == "completed"
        assert not (child / "provider-calls.txt").exists()
        status = read_status_at(run / ".state/tasks/depth" / ticker)
        assert status is not None and status.state == "deferred"
    assert not (run / "depth/CCC").exists()
    root_status = read_yaml_file(run / ".state/process-status.yaml")
    assert root_status["selected_scope_step"].startswith("depth/batch-")
    assert root_status["selected_scope_steps"] == ["depth/AFL/query", "depth/BBB/query"]
    assert root_status["selected_scope_mode"] == "through"

    # The mapped item itself is a valid through-target once its child outputs can
    # be completed. Invalid allowlists must reject before touching either item.
    for rejected in (
        ["depth/DDD", "depth/AFL"],
        ["depth/AFL", "depth/AFL"],
        ["depth/AFL", "depth/BBB/query"],
    ):
        invalid_args = [*args, "--only", "depth", "--scope-through-target"]
        for path in rejected:
            invalid_args.extend(["--only-scope-step", path])
        invalid = runner.invoke(app, invalid_args)
        assert invalid.exit_code != 0
        assert (run / "depth/AFL/query/planner-calls.txt").read_text() == "x"
        assert (run / "depth/BBB/query/planner-calls.txt").read_text() == "x"
        assert not (run / "depth/CCC").exists()

    whole_items = [
        *args,
        "--only",
        "depth",
        "--scope-through-target",
        "--only-scope-step",
        "depth/AFL",
        "--only-scope-step",
        "depth/BBB",
    ]
    for _ in range(2):
        full = runner.invoke(app, whole_items)
        assert full.exit_code == 0, full.output
    for ticker in ("AFL", "BBB"):
        child = run / "depth" / ticker
        assert (child / "query/planner-calls.txt").read_text() == "x"
        assert (child / "provider-calls.txt").read_text() == "x"
        mapped = read_status_at(run / ".state/tasks/depth" / ticker)
        assert mapped is not None and mapped.state == "completed"
        result = read_yaml_file(run / ".state/tasks/depth" / ticker / "result.yaml")
        assert result["validated"] is True
    assert not (run / "depth/CCC").exists()
    scoped = read_yaml_file(run / ".state/process-status.yaml")
    assert scoped["selected_scope_steps"] == ["depth/AFL", "depth/BBB"]
    assert scoped["selected_scope_mode"] == "through"
    ordinary = runner.invoke(app, args)
    assert ordinary.exit_code == 0, ordinary.output
    assert (run / "depth/CCC/final.txt").read_text() == "final"
    assert (run / "depth/AFL/query/planner-calls.txt").read_text() == "x"
    assert (run / "depth/BBB/query/planner-calls.txt").read_text() == "x"
    assert (run / "depth/AFL/provider-calls.txt").read_text() == "x"
    assert (run / "depth/BBB/provider-calls.txt").read_text() == "x"
    assert "selected_scope_step" not in read_yaml_file(run / ".state/process-status.yaml")


def test_partial_status_preserves_even_invalid_prior_scope_mode(tmp_path: Path) -> None:
    status_path = tmp_path / ".state/process-status.yaml"
    status_path.parent.mkdir()
    status_path.write_text(
        "selected_scope_step: depth/AFL/query\nselected_scope_mode: null\n",
        encoding="utf-8",
    )
    _write_process_status(
        tmp_path,
        "synthetic",
        {"depth": {"state": "running"}},
        "2026-10-01T00:00:00",
        active_step_ids={"depth"},
    )
    partial = read_yaml_file(status_path)
    assert partial["selected_scope_step"] == "depth/AFL/query"
    assert partial["selected_scope_mode"] == ""
    _write_process_status(
        tmp_path,
        "synthetic",
        {"depth": {"state": "completed"}},
        "2026-10-01T00:00:00",
        active_step_ids={"depth"},
        clear_selected_scope_step=True,
    )
    complete = read_yaml_file(status_path)
    assert "selected_scope_step" not in complete
    assert "selected_scope_mode" not in complete
