"""A mapped composite opens as many item scopes as the run's ceiling admits."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import run_process as run_process_module
from metaproc.engine import item_runner

PROBE = """\
import pathlib, sys, time
probe = pathlib.Path(sys.argv[2])
item = sys.argv[1]
(probe / f"start-{item}").touch()
live = len(list(probe.glob("start-*"))) - len(list(probe.glob("end-*")))
(probe / f"live-{item}").write_text(str(live))
time.sleep(0.2)
(probe / f"end-{item}").touch()
"""


def _mapped_process(tmp_path: Path, items: int) -> tuple[Path, Path]:
    probe = tmp_path / "probe"
    probe.mkdir()
    (tmp_path / "probe.py").write_text(PROBE)
    (tmp_path / "child.process.md").write_text(
        "---\nprocess:\n  name: child\n  steps:\n"
        "    - id: hold\n      mode: code\n"
        f"      command: '{sys.executable} {tmp_path / 'probe.py'} {{{{item}}}} {probe}'\n---\n"
    )
    rows = "".join(f"    - item: i{index:02d}\n" for index in range(items))
    (tmp_path / "items.md").write_text(f"---\nprogress:\n  items:\n{rows}---\n")
    process = tmp_path / "parent.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: parent
          deps:
            child: {path: ./child.process.md, as: path}
            items: {path: ./items.md, as: path}
          steps:
            - id: child
              mode: composite
              uses: deps.child
              for_each:
                over: deps.items
                bind: item
                bind_fields: [item]
                key: "{{item}}"
        ---
        """)
    )
    return process, probe


def _run(tmp_path: Path, process: Path, max_concurrency: int) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=scopes",
            "--max-concurrency",
            str(max_concurrency),
            "--initial-concurrency",
            str(max_concurrency),
        ],
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(("ceiling", "scopes"), [(50, 50), (160, 160), (8, 32)])
def test_a_mapped_composite_opens_scopes_up_to_the_run_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ceiling: int, scopes: int
) -> None:
    """The scope limit handed to the fan-out follows the run's ceiling, floored at 32.

    Measured end to end before this limit followed the ceiling: forty two-second items
    under a ceiling of 50 never had more than 32 scopes open at once.
    """
    seen: list[int | None] = []
    real_fan_out = item_runner.run_fan_out

    async def recording_fan_out(**kwargs: Any) -> tuple[int, int]:
        seen.append(kwargs.get("max_concurrency"))
        return await real_fan_out(**kwargs)

    monkeypatch.setattr(run_process_module, "run_fan_out", recording_fan_out)
    process, probe = _mapped_process(tmp_path, items=3)
    _run(tmp_path, process, max_concurrency=ceiling)
    assert seen == [scopes]
    assert len(list(probe.glob("end-*"))) == 3


def test_mapped_scope_concurrency_never_drops_below_the_floor() -> None:
    floor = run_process_module._DEFAULT_MAPPED_SCOPE_CONCURRENCY
    assert run_process_module._mapped_scope_concurrency(None) == floor
    assert run_process_module._mapped_scope_concurrency(4) == floor
    assert run_process_module._mapped_scope_concurrency(160) == 160
