"""Resume reuse versus collected fan-in inputs.

A step that declares a ``collect:`` input is handed a fan-in document that the
orchestrator rebuilds from the collected mapped step's per-item state. When a resume
finishes an item that had failed, that document changes while the consumer's
fingerprint does not. The consumer, and everything downstream of it, must re-run rather
than reuse work computed over the old outcomes. A resume that changes nothing must
still reuse everything.

The end-to-end tests drive ``metaproc run-process`` against one ``RUN_ID``: a launch in
which one mapped item fails while a marker file exists, then a resume after the marker
is removed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import textwrap
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from metaproc.cli import app
from metaproc.engine.fan_in import build_outcome_manifest
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.state_io import read_collected_inputs_at, read_status_at
from metaproc.paths import COLLECTED_INPUTS_FILE, STATE_DIR, TASKS_SUBDIR

_HANDLERS = '''\
"""Code handlers for the collected-input reuse tests."""

from __future__ import annotations

from pathlib import Path

import yaml


def _log(variables: dict[str, str], name: str) -> None:
    # A composite child's RUN_ID extends the root's, so every scope logs to one file.
    root = Path(variables["RUNS_DIR"]) / variables["RUN_ID"].split("/")[0]
    with (root / "invocations.log").open("a") as fh:
        fh.write(name + "\\n")


def _succeeded(step: object) -> list[str]:
    manifest = yaml.safe_load(Path(step.inputs["outcomes"].path).read_text())
    return sorted(i["key"] for i in manifest["fan_in_outcomes"]["items"] if i["succeeded"])


def _write(step: object, name: str, text: str) -> None:
    out = Path(step.outputs[name].path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)


def scan(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    item = variables["item"]
    _log(variables, f"scan:{item}")
    marker = Path(variables["RUNS_DIR"]) / f"fail-{item}"
    if marker.exists():
        detail = ""
        if marker.read_text().strip() == "vary":
            # A marker reading `vary` makes each attempt fail with different wording.
            root = Path(variables["RUNS_DIR"]) / variables["RUN_ID"].split("/")[0]
            attempts = (root / "invocations.log").read_text().splitlines()
            detail = f" (attempt {attempts.count(f'scan:{item}')})"
        raise RuntimeError(f"{item}: failing while its marker exists{detail}")
    out = Path(variables["RUNS_DIR"]) / variables["RUN_ID"] / "scan" / f"{item}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(item + "\\n")


def prep(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    item = variables["item"]
    _log(variables, f"prep:{item}")
    if (Path(variables["RUNS_DIR"]) / f"fail-{item}").exists():
        raise RuntimeError(f"{item}: prep failing while its marker exists")
    out = Path(variables["RUNS_DIR"]) / variables["RUN_ID"] / "prep" / f"{item}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(item + "\\n")


def summarize(variables: dict[str, str], step: object) -> None:
    _log(variables, "summarize")
    _write(step, "summary", ",".join(_succeeded(step)) + "\\n")


def select(variables: dict[str, str], step: object) -> None:
    _log(variables, "select")
    _write(step, "summary", ",".join(_succeeded(step)) + "\\n")


def tally(variables: dict[str, str], step: object) -> None:
    _log(variables, "tally")
    _write(step, "tally", f"{len(_succeeded(step))}\\n")


def report(variables: dict[str, str], step: object) -> None:
    _log(variables, "report")
    _write(step, "report", "report:" + Path(step.inputs["summary"].path).read_text())


def build_roster(variables: dict[str, str], step: object) -> None:
    _log(variables, "roster")
    items = "".join(f"    - item: {key}\\n" for key in _succeeded(step))
    header = "---\\nprogress:\\n  schema: metaproc:ProgressSpec/0.1\\n  process: enrich\\n"
    _write(step, "roster", header + "  items:\\n" + items + "---\\n# Enrich Roster\\n")


def fetch(variables: dict[str, str], step: object) -> None:
    # Stands in for a paid provider call: re-running it for an unchanged item re-buys data.
    _log(variables, f"fetch:{variables['item']}")
    _write(step, "fetched", variables["item"] + "\\n")


def fetch_code(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    # The same fetch as a mapped `code` step: a mapped step's declared output path still
    # holds `{{item}}`, so the item's own path comes from its bound variables.
    item = variables["item"]
    _log(variables, f"fetch:{item}")
    out = Path(variables["RUNS_DIR"]) / variables["RUN_ID"] / "enrich" / item / "fetched.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(item + "\\n")


def stage_one(variables: dict[str, str], step: object) -> None:
    _log(variables, f"s1:{variables['item']}")
    _write(step, "out", variables["item"] + "\\n")


def stage_two(variables: dict[str, str], step: object) -> None:
    _log(variables, "s2")
    _write(step, "summary", "s2\\n")
'''

_ROSTER = """\
---
progress:
  schema: metaproc:ProgressSpec/0.1
  process: collect-reuse
  items:
    - item: a
    - item: b
---
# Collect Reuse Roster

Two synthetic items; a test fails one of them by creating its marker file.
"""

_SCAN = """\
- id: scan
  mode: code
  handler: "handlers.py:scan"
  for_each:
    over: deps.roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
  outputs:
    out: { path: "{{run.dir}}/scan/{{item}}.txt", kind: file }
"""

_SUMMARIZE = """\
- id: summarize
  mode: code
  handler: "handlers.py:summarize"
  needs: [scan]
  inputs:
    outcomes:
      path: "{{run.dir}}/summary/outcomes.yaml"
      collect: scan
      require: finished
  outputs:
    summary: { path: "{{run.dir}}/summary/summary.txt", kind: file }
"""

_TALLY = """\
- id: tally
  mode: code
  handler: "handlers.py:tally"
  needs: [scan]
  inputs:
    outcomes:
      path: "{{run.dir}}/tally/outcomes.yaml"
      collect: scan
      require: finished
  outputs:
    tally: { path: "{{run.dir}}/tally/tally.txt", kind: file }
"""


# `scan` item-aligned under `prep`: an item whose `prep` fails never reaches `scan`, so
# the fan-in document reports it `not_reached` and names where its chain stopped.
_ALIGNED_CHAIN = """\
- id: prep
  mode: code
  handler: "handlers.py:prep"
  on_failure: continue
  for_each:
    over: deps.roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
  outputs:
    out: { path: "{{run.dir}}/prep/{{item}}.txt", kind: file }
- id: scan
  mode: code
  handler: "handlers.py:scan"
  needs: [prep]
  on_failure: continue
  for_each:
    over: deps.roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
    align: same_key
  outputs:
    out: { path: "{{run.dir}}/scan/{{item}}.txt", kind: file }
- id: summarize
  mode: code
  handler: "handlers.py:summarize"
  needs: [scan]
  on_failure: continue
  inputs:
    outcomes:
      path: "{{run.dir}}/summary/outcomes.yaml"
      collect: scan
      require: finished
  outputs:
    summary: { path: "{{run.dir}}/summary/summary.txt", kind: file }
"""


def _report(upstream: str) -> str:
    # `on_failure: continue` lets the downstream step run over partial outcomes on the
    # launch, as a rollup over a partly failed fan-out typically does.
    return f"""\
- id: report
  mode: code
  handler: "handlers.py:report"
  needs: [{upstream}]
  on_failure: continue
  inputs:
    summary: {{ ref: {upstream}.summary }}
  outputs:
    report: {{ path: "{{{{run.dir}}}}/report.txt", kind: file }}
"""


# The consumer is a composite whose own `collect:` input is handed to its child process
# through `with:`; the child step that reads it sees a plain file input.
_CONSUME = """\
- id: consume
  mode: composite
  uses: deps.consume_process
  needs: [scan]
  inputs:
    outcomes:
      path: "{{run.dir}}/consume/outcomes.yaml"
      collect: scan
      require: finished
  with:
    outcomes: "{{run.dir}}/consume/outcomes.yaml"
  outputs:
    summary: { path: "{{run.dir}}/consume/summary.txt", kind: file }
"""

_CONSUME_CHILD = """\
process:
  name: consume
  inputs:
    outcomes: { param: OUTCOMES, as: path }
  outputs:
    summary: { ref: select.summary, as: path }
  steps:
    - id: select
      mode: code
      handler: "handlers.py:select"
      inputs:
        outcomes: { path: "{{outcomes}}", kind: file }
      outputs:
        summary: { path: "{{run.dir}}/summary.txt", kind: file }
"""

# The consumer is a step inside the child process, collecting a sibling mapped step.
_STAGE = """\
- id: stage
  mode: composite
  uses: deps.stage_process
  outputs:
    report: { path: "{{run.dir}}/stage/report.txt", kind: file }
"""


# A composite collector whose child writes the roster a downstream mapped composite
# fans out over, and whose items each hold a counted provider-style fetch.
_ROSTER_CONSUME = """\
- id: consume
  mode: composite
  uses: deps.consume_process
  needs: [scan]
  inputs:
    outcomes:
      path: "{{run.dir}}/consume/outcomes.yaml"
      collect: scan
      require: finished
  with:
    outcomes: "{{run.dir}}/consume/outcomes.yaml"
  outputs:
    roster: { path: "{{run.dir}}/consume/enrich-roster.md", kind: file }
"""

_ROSTER_CONSUME_CHILD = """\
process:
  name: consume
  inputs:
    outcomes: { param: OUTCOMES, as: path }
  outputs:
    roster: { ref: build-roster.roster, as: path }
  steps:
    - id: build-roster
      mode: code
      handler: "handlers.py:build_roster"
      inputs:
        outcomes: { path: "{{outcomes}}", kind: file }
      outputs:
        roster: { path: "{{run.dir}}/enrich-roster.md", kind: file }
"""

_ENRICH = """\
- id: enrich
  mode: composite
  uses: deps.enrich_process
  needs: [consume]
  on_failure: continue
  inputs:
    roster: { ref: consume.roster }
  for_each:
    over: roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
  with:
    item: "{{item}}"
  outputs:
    fetched: { path: "{{run.dir}}/enrich/{{item}}/fetched.txt", kind: file }
"""

# The same downstream shape with a mapped `code` step in place of the mapped composite:
# here the per-item record is the fetch itself, not a parent level over reusable children.
_ENRICH_CODE = """\
- id: enrich
  mode: code
  handler: "handlers.py:fetch_code"
  needs: [consume]
  on_failure: continue
  inputs:
    roster: { ref: consume.roster }
  for_each:
    over: roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
  outputs:
    fetched: { path: "{{run.dir}}/enrich/{{item}}/fetched.txt", kind: file }
"""

_ENRICH_CHILD = """\
process:
  name: enrich
  inputs:
    item: { param: ITEM, as: string }
  outputs:
    fetched: { ref: fetch.fetched, as: path }
  steps:
    - id: fetch
      mode: code
      handler: "handlers.py:fetch"
      outputs:
        fetched: { path: "{{run.dir}}/fetched.txt", kind: file }
"""

# A mapped composite, a scalar composite downstream of it, and a leaf after both.
_STAGE_ONE = """\
- id: stage1
  mode: composite
  uses: deps.stage1_process
  for_each:
    over: deps.roster
    bind: item
    bind_fields: [item]
    key: "{{item}}"
  with:
    item: "{{item}}"
  outputs:
    out: { path: "{{run.dir}}/stage1/{{item}}/out.txt", kind: file }
"""

_STAGE_ONE_CHILD = """\
process:
  name: stage1
  inputs:
    item: { param: ITEM, as: string }
  outputs:
    out: { ref: s1.out, as: path }
  steps:
    - id: s1
      mode: code
      handler: "handlers.py:stage_one"
      outputs:
        out: { path: "{{run.dir}}/out.txt", kind: file }
"""

_STAGE_TWO = """\
- id: stage2
  mode: composite
  uses: deps.stage2_process
  needs: [stage1]
  outputs:
    summary: { path: "{{run.dir}}/stage2/summary.txt", kind: file }
"""

_STAGE_TWO_CHILD = """\
process:
  name: stage2
  outputs:
    summary: { ref: s2.summary, as: path }
  steps:
    - id: s2
      mode: code
      handler: "handlers.py:stage_two"
      outputs:
        summary: { path: "{{run.dir}}/summary.txt", kind: file }
"""


def _spec_document(name: str, body: str) -> str:
    return f"---\n{body}---\n# {name}\n"


def _process_block(name: str, deps: str, steps: list[str], extra: str = "") -> str:
    return (
        "process:\n"
        f"  name: {name}\n"
        + textwrap.indent(extra, "  ")
        + "  deps:\n"
        + textwrap.indent(deps, "    ")
        + "  steps:\n"
        + "".join(textwrap.indent(step, "    ") for step in steps)
    )


def _write_process(process_dir: Path, shape: str) -> Path:
    """Write the handlers, roster, and the process for one consumer *shape*.

    - ``top-level``: ``scan`` -> ``summarize`` (collects scan) -> ``report``.
    - ``two-consumers``: ``top-level`` plus ``tally``, a second collector of scan.
    - ``composite``: ``scan`` -> ``consume`` (a composite that collects scan and
      hands the document to its child step ``select``) -> ``report``.
    - ``child-collector``: one composite ``stage`` whose child process is the
      ``top-level`` shape, so the collector is a child step.
    - ``downstream-mapped``: ``scan`` -> ``consume`` (a composite collector whose
      child writes a roster) -> ``enrich`` (a mapped composite over that roster whose
      items each run a counted ``fetch``).
    - ``downstream-mapped-code``: the same, with ``enrich`` a mapped ``code`` step
      whose items each run the counted ``fetch`` directly.
    - ``fingerprint``: ``stage1`` (a mapped composite) -> ``stage2`` (a scalar
      composite) -> ``report``; no collector.
    - ``aligned-chain``: ``prep`` -> ``scan`` (item-aligned under ``prep``) ->
      ``summarize`` (collects scan) -> ``report``.
    """
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    (process_dir / "roster.md").write_text(_ROSTER, encoding="utf-8")
    roster_dep = "roster: { path: ./roster.md, as: path }\n"
    if shape in ("top-level", "two-consumers"):
        steps = [_SCAN, _SUMMARIZE, _report("summarize")]
        if shape == "two-consumers":
            steps.append(_TALLY)
        body = _process_block("collect-reuse", roster_dep, steps)
    elif shape == "composite":
        (process_dir / "consume.process.md").write_text(
            _spec_document("Consume", _CONSUME_CHILD), encoding="utf-8"
        )
        deps = roster_dep + "consume_process: { path: ./consume.process.md, as: path }\n"
        body = _process_block("collect-reuse", deps, [_SCAN, _CONSUME, _report("consume")])
    elif shape == "child-collector":
        child = _process_block(
            "stage",
            roster_dep,
            [_SCAN, _SUMMARIZE, _report("summarize")],
            extra="outputs:\n  report: { ref: report.report, as: path }\n",
        )
        (process_dir / "stage.process.md").write_text(
            _spec_document("Stage", child), encoding="utf-8"
        )
        deps = "stage_process: { path: ./stage.process.md, as: path }\n"
        body = _process_block("collect-reuse", deps, [_STAGE])
    elif shape == "downstream-mapped":
        for name, child in (("consume", _ROSTER_CONSUME_CHILD), ("enrich", _ENRICH_CHILD)):
            (process_dir / f"{name}.process.md").write_text(
                _spec_document(name.title(), child), encoding="utf-8"
            )
        deps = (
            roster_dep
            + "consume_process: { path: ./consume.process.md, as: path }\n"
            + "enrich_process: { path: ./enrich.process.md, as: path }\n"
        )
        body = _process_block("collect-reuse", deps, [_SCAN, _ROSTER_CONSUME, _ENRICH])
    elif shape == "downstream-mapped-code":
        (process_dir / "consume.process.md").write_text(
            _spec_document("Consume", _ROSTER_CONSUME_CHILD), encoding="utf-8"
        )
        deps = roster_dep + "consume_process: { path: ./consume.process.md, as: path }\n"
        body = _process_block("collect-reuse", deps, [_SCAN, _ROSTER_CONSUME, _ENRICH_CODE])
    elif shape == "aligned-chain":
        body = _process_block("collect-reuse", roster_dep, [_ALIGNED_CHAIN, _report("summarize")])
    elif shape == "fingerprint":
        for name, child in (("stage1", _STAGE_ONE_CHILD), ("stage2", _STAGE_TWO_CHILD)):
            (process_dir / f"{name}.process.md").write_text(
                _spec_document(name.title(), child), encoding="utf-8"
            )
        deps = (
            roster_dep
            + "stage1_process: { path: ./stage1.process.md, as: path }\n"
            + "stage2_process: { path: ./stage2.process.md, as: path }\n"
        )
        body = _process_block("collect-reuse", deps, [_STAGE_ONE, _STAGE_TWO, _report("stage2")])
    else:
        raise ValueError(shape)
    path = process_dir / "collect-reuse.process.md"
    path.write_text(_spec_document("Collect Reuse", body), encoding="utf-8")
    return path


def _run(process_path: Path, runs_dir: Path, run_id: str, *options: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            f"RUN_ID={run_id}",
            "--backend",
            "local",
            *options,
        ],
    )


def _message(result: Result) -> str:
    return f"{result.output}\n{result.exception}"


def _invocations(run_dir: Path) -> Counter[str]:
    # Mapped items run concurrently, so compare counts rather than order.
    return Counter((run_dir / "invocations.log").read_text().splitlines())


def _record_dir(run_dir: Path, step_id: str) -> Path:
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id


def _step_states(run_dir: Path) -> dict[str, str]:
    """Each top-level step's state as ``metaproc status --steps`` reports it."""
    result = CliRunner().invoke(app, ["status", str(run_dir), "--steps", "--format", "json"])
    assert result.exit_code == 0, _message(result)
    return {entry["step_id"]: entry["state"] for entry in json.loads(result.output)["steps"]}


def _setup(tmp_path: Path, shape: str, *, failing: str | None) -> tuple[Path, Path, Path]:
    process_path = _write_process(tmp_path / "proc", shape)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    if failing is not None:
        (runs_dir / f"fail-{failing}").touch()
    run_id = "collect-run"
    return process_path, runs_dir, runs_dir / run_id


# ── End to end through run-process ────────────────────────────────


def test_resume_reruns_a_consumer_whose_collected_input_changed(tmp_path: Path) -> None:
    process_path, runs_dir, run_dir = _setup(tmp_path, "top-level", failing="b")

    launched = _run(process_path, runs_dir, run_dir.name)

    # The mapped step fails on one item; the consumer and its downstream still run over
    # the partial outcomes.
    assert launched.exit_code != 0, _message(launched)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 1, "summarize": 1, "report": 1})
    assert (run_dir / "report.txt").read_text() == "report:a\n"

    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert (
        "Step 'summarize': collected input 'outcomes' from 'scan' changed since the step "
        "last ran — invalidated: summarize, report"
    ) in resumed.output
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 2, "summarize": 2, "report": 2})
    assert (run_dir / "summary" / "summary.txt").read_text() == "a,b\n"
    assert (run_dir / "report.txt").read_text() == "report:a,b\n"
    # The invalidation left `status.yaml.stale` beside each re-run step's new record;
    # status reports the steps by their new completion, not by the leftover file.
    assert (_record_dir(run_dir, "summarize") / "status.yaml.stale").exists()
    assert _step_states(run_dir) == {"scan": "current", "summarize": "current", "report": "current"}


@pytest.mark.parametrize(
    ("shape", "consumer", "manifest"),
    [
        ("top-level", "summarize", "summary/outcomes.yaml"),
        ("composite", "consume", "consume/outcomes.yaml"),
    ],
)
def test_unchanged_resume_reuses_every_step(
    tmp_path: Path, shape: str, consumer: str, manifest: str
) -> None:
    process_path, runs_dir, run_dir = _setup(tmp_path, shape, failing=None)
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code == 0, _message(launched)

    document = run_dir / manifest
    record = read_collected_inputs_at(_record_dir(run_dir, consumer))
    assert record is not None
    rebuilt = build_outcome_manifest(run_dir, "scan", expected_keys=["a", "b"])
    assert record.inputs["outcomes"].outcomes_sha256 == rebuilt.outcomes_sha256
    before = _invocations(run_dir)
    content = document.read_bytes()
    # Age the document so a rewrite on resume is observable.
    os.utime(document, ns=(0, 0))

    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert "changed since the step last ran" not in resumed.output
    assert _invocations(run_dir) == before
    assert read_collected_inputs_at(_record_dir(run_dir, consumer)) == record
    assert document.read_bytes() == content
    if shape == "composite":
        # A composite is re-entered on every resume and rewrites its document: the mtime
        # moves while the content does not, and the step is still reused.
        assert document.stat().st_mtime_ns != 0


@pytest.mark.parametrize("selection", ["--only", "--from"])
def test_a_selected_collector_is_handed_the_document_a_full_walk_builds(
    tmp_path: Path, selection: str
) -> None:
    """A ``--only``/``--from`` selection does not change a collector's fan-in document.

    ``prep:b`` fails on every attempt, so ``b`` never reaches ``scan``, and nothing about
    ``scan`` changes between launches. Built from the selection, which leaves ``scan``
    and its chain out, the document dropped ``b``; the collector then read as changed
    and re-ran, and the next plain resume re-ran it and ``report`` back.
    """
    process_path, runs_dir, run_dir = _setup(tmp_path, "aligned-chain", failing="b")
    outputs: list[str] = []
    for options in ((), (selection, "summarize"), (), (selection, "summarize"), ()):
        result = _run(process_path, runs_dir, run_dir.name, *options)
        assert result.exception is None or isinstance(result.exception, (SystemExit, CLIError)), (
            _message(result)
        )
        outputs.append(result.output)

    assert not [output for output in outputs if "changed since the step last ran" in output]
    invocations = _invocations(run_dir)
    assert (invocations["summarize"], invocations["report"]) == (1, 1)

    # Forced, the collector runs over the whole roster, `b` included.
    forced = _run(process_path, runs_dir, run_dir.name, selection, "summarize", "--force")
    assert forced.exception is None or isinstance(forced.exception, (SystemExit, CLIError)), (
        _message(forced)
    )
    assert _invocations(run_dir)["summarize"] == 2
    assert "Collected 'outcomes' from 'scan': 1 succeeded, 1 failed of 2" in forced.output
    outcomes = read_yaml_file(run_dir / "summary" / "outcomes.yaml")["fan_in_outcomes"]
    assert outcomes["total"] == 2
    assert {item["key"]: item["state"] for item in outcomes["items"]} == {
        "a": "completed",
        "b": "not_reached",
    }


def test_resume_reruns_a_composite_consumers_child_steps(tmp_path: Path) -> None:
    process_path, runs_dir, run_dir = _setup(tmp_path, "composite", failing="b")

    launched = _run(process_path, runs_dir, run_dir.name)

    assert launched.exit_code != 0, _message(launched)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 1, "select": 1, "report": 1})
    assert (run_dir / "report.txt").read_text() == "report:a\n"

    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert (
        "Step 'consume': collected input 'outcomes' from 'scan' changed since the step "
        "last ran — invalidated: consume, report"
    ) in resumed.output
    # The child step reads the collected document as a plain file input; it re-runs
    # because its enclosing composite was invalidated.
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 2, "select": 2, "report": 2})
    assert (run_dir / "consume" / "summary.txt").read_text() == "a,b\n"
    assert (run_dir / "report.txt").read_text() == "report:a,b\n"

    again = _run(process_path, runs_dir, run_dir.name)

    assert again.exit_code == 0, _message(again)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 2, "select": 2, "report": 2})


def test_resume_reruns_a_collector_inside_a_composite_child(tmp_path: Path) -> None:
    process_path, runs_dir, run_dir = _setup(tmp_path, "child-collector", failing="b")

    launched = _run(process_path, runs_dir, run_dir.name)

    assert launched.exit_code != 0, _message(launched)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 1, "summarize": 1, "report": 1})

    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert (
        "Step 'summarize': collected input 'outcomes' from 'scan' changed since the step "
        "last ran — invalidated: summarize, report"
    ) in resumed.output
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 2, "summarize": 2, "report": 2})
    assert (run_dir / "stage" / "report.txt").read_text() == "report:a,b\n"


def test_a_consumer_without_recorded_digests_is_not_invalidated(tmp_path: Path) -> None:
    process_path, runs_dir, run_dir = _setup(tmp_path, "two-consumers", failing="b")
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code != 0, _message(launched)
    assert (run_dir / "tally" / "tally.txt").read_text() == "1\n"

    # `summarize` looks like a completion recorded before digests existed; `tally`
    # keeps its record.
    (_record_dir(run_dir, "summarize") / COLLECTED_INPUTS_FILE).unlink(missing_ok=True)
    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    # The legacy completion and its downstream are reused, as before digests existed.
    assert "Step 'summarize': collected input" not in resumed.output
    assert (run_dir / "report.txt").read_text() == "report:a\n"
    # The consumer that has a record is re-run over the full outcomes.
    assert "Step 'tally': collected input 'outcomes' from 'scan' changed" in resumed.output
    assert (run_dir / "tally" / "tally.txt").read_text() == "2\n"
    assert _invocations(run_dir) == Counter(
        {"scan:a": 1, "scan:b": 2, "summarize": 1, "report": 1, "tally": 2}
    )


def test_a_downstream_mapped_composite_reuses_its_completed_items_child_steps(
    tmp_path: Path,
) -> None:
    """Only the direct consumer's children re-run; downstream composites keep theirs.

    Re-running an unchanged downstream item's fetch would re-buy provider data. Each
    downstream item re-enters its composite and reuses its completed child steps, and
    only an item the new roster adds does work.
    """
    process_path, runs_dir, run_dir = _setup(tmp_path, "downstream-mapped", failing="b")

    launched = _run(process_path, runs_dir, run_dir.name)

    assert launched.exit_code != 0, _message(launched)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 1, "roster": 1, "fetch:a": 1})

    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert (
        "Step 'consume': collected input 'outcomes' from 'scan' changed since the step "
        "last ran — invalidated: consume, enrich"
    ) in resumed.output
    # The collector's child re-runs over the full outcomes; item a's fetch is reused and
    # only the added item b fetches.
    assert _invocations(run_dir) == Counter(
        {"scan:a": 1, "scan:b": 2, "roster": 2, "fetch:a": 1, "fetch:b": 1}
    )
    assert (run_dir / "enrich" / "b" / "fetched.txt").read_text() == "b\n"


def test_a_downstream_mapped_code_step_reuses_its_completed_items(tmp_path: Path) -> None:
    """A downstream mapped non-composite step keeps its completed items' records.

    For a mapped composite the per-item record is a parent level over child steps that
    are reused; for a mapped non-composite step it is the work itself, so renaming it
    would re-run every completed item's fetch on every routine backfill. The step is still re-entered, so the roster's new item is discovered.
    """
    process_path, runs_dir, run_dir = _setup(tmp_path, "downstream-mapped-code", failing="b")

    launched = _run(process_path, runs_dir, run_dir.name)

    assert launched.exit_code != 0, _message(launched)
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 1, "roster": 1, "fetch:a": 1})

    (runs_dir / "fail-b").unlink()
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    # The mapped `code` step keeps every item record, so the cascade renames nothing of
    # its own and it is not named among the invalidated steps.
    assert (
        "Step 'consume': collected input 'outcomes' from 'scan' changed since the step "
        "last ran — invalidated: consume"
    ) in resumed.output
    # It is still re-entered, so the roster's added item is discovered and the completed
    # item is reused rather than re-fetched.
    assert "Step 'enrich': 1 actionable items (1 reused)" in resumed.output
    assert _invocations(run_dir) == Counter(
        {"scan:a": 1, "scan:b": 2, "roster": 2, "fetch:a": 1, "fetch:b": 1}
    )
    assert (run_dir / "enrich" / "b" / "fetched.txt").read_text() == "b\n"


def test_a_fingerprint_change_keeps_composites_completed_child_steps(tmp_path: Path) -> None:
    """Editing a composite's process file invalidates it and its downstream, and the
    completed steps inside every composite are reused."""
    process_path, runs_dir, run_dir = _setup(tmp_path, "fingerprint", failing=None)
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code == 0, _message(launched)
    assert _invocations(run_dir) == Counter({"s1:a": 1, "s1:b": 1, "s2": 1, "report": 1})

    child_spec = process_path.parent / "stage1.process.md"
    child_spec.write_text(child_spec.read_text() + "\nEdited prose.\n", encoding="utf-8")
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    assert "Step 'stage1': fingerprint changed" in resumed.output
    # The leaf downstream re-runs; no composite's completed child step does.
    assert _invocations(run_dir) == Counter({"s1:a": 1, "s1:b": 1, "s2": 1, "report": 2})


def _item_error(run_dir: Path, step_id: str, key: str) -> str:
    status = read_status_at(_record_dir(run_dir, step_id) / key)
    assert status is not None
    assert status.error is not None
    return status.error


def test_a_repeated_failure_names_the_new_log_and_reuses_the_consumer(
    tmp_path: Path,
) -> None:
    """The document carries each item's full error, attempt log path included; reuse
    follows the outcome digest, which a new log path does not move."""
    process_path, runs_dir, run_dir = _setup(tmp_path, "composite", failing="b")
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code != 0, _message(launched)
    document = run_dir / "consume" / "outcomes.yaml"
    first_error = _item_error(run_dir, "scan", "b")
    assert "(traceback: " in first_error
    record = read_collected_inputs_at(_record_dir(run_dir, "consume"))
    assert record is not None

    # The item fails again the same way, under a new attempt. The composite consumer
    # is re-entered, so its document is written again.
    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code != 0, _message(resumed)
    second_error = _item_error(run_dir, "scan", "b")
    assert first_error != second_error
    items = read_yaml_file(document)["fan_in_outcomes"]["items"]
    failed = next(item for item in items if item["key"] == "b")
    assert failed["error"] == second_error
    assert first_error not in document.read_text(encoding="utf-8")
    # Same outcomes, so the record stands and the consumer's child step is reused.
    assert read_collected_inputs_at(_record_dir(run_dir, "consume")) == record
    assert "Step 'consume': collected input" not in resumed.output
    assert _invocations(run_dir) == Counter({"scan:a": 1, "scan:b": 2, "select": 1, "report": 1})


@pytest.mark.parametrize(
    ("shape", "consumer", "manifest"),
    [
        ("top-level", "summarize", "summary/outcomes.yaml"),
        ("composite", "select", "consume/outcomes.yaml"),
    ],
)
def test_a_failed_item_that_fails_again_differently_does_not_rerun_its_consumer(
    tmp_path: Path, shape: str, consumer: str, manifest: str
) -> None:
    """Invalidation follows what happened to each item, not how its error reads."""
    process_path, runs_dir, run_dir = _setup(tmp_path, shape, failing="b")
    (runs_dir / "fail-b").write_text("vary\n")
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code != 0, _message(launched)
    before = _invocations(run_dir)
    first_error = _item_error(run_dir, "scan", "b")

    resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code != 0, _message(resumed)
    assert "(attempt 1)" in first_error
    assert "(attempt 2)" in _item_error(run_dir, "scan", "b")
    assert "collected input" not in resumed.output
    assert _invocations(run_dir) == before + Counter({"scan:b": 1})
    assert _invocations(run_dir)[consumer] == 1
    if shape == "composite":
        # The re-entered consumer was handed the new wording without re-running.
        items = read_yaml_file(run_dir / manifest)["fan_in_outcomes"]["items"]
        assert "(attempt 2)" in next(item for item in items if item["key"] == "b")["error"]


@pytest.mark.parametrize("earlier_shape", [False, True], ids=["missing", "byte-digest"])
def test_a_record_missing_the_outcome_digest_counts_as_changed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, earlier_shape: bool
) -> None:
    """A record without ``outcomes_sha256`` fails validation. Present but unreadable
    is not absent: its consumer cannot be compared, so it counts as changed and
    re-runs with its downstream, and the next delivery replaces the record."""
    process_path, runs_dir, run_dir = _setup(tmp_path, "two-consumers", failing="b")
    launched = _run(process_path, runs_dir, run_dir.name)
    assert launched.exit_code != 0, _message(launched)

    # Rewrite `summarize`'s record without the outcome digest, or in the earlier shape
    # that held the digest of the document's bytes instead; `tally` keeps its record.
    state_dir = _record_dir(run_dir, "summarize")
    record_path = state_dir / COLLECTED_INPUTS_FILE
    record = read_yaml_file(record_path)
    for entry in record["inputs"].values():
        del entry["outcomes_sha256"]
        if earlier_shape:
            document = Path(entry["path"])
            entry["sha256"] = hashlib.sha256(document.read_bytes()).hexdigest()
    record_path.write_text(to_yaml_string(record), encoding="utf-8")
    (runs_dir / "fail-b").unlink()

    with caplog.at_level(logging.WARNING, logger="metaproc.commands.run_process"):
        resumed = _run(process_path, runs_dir, run_dir.name)

    assert resumed.exit_code == 0, _message(resumed)
    # The operator sees the decision beside the other resume decisions, and the log
    # keeps the traceback.
    assert (
        f"Step 'summarize': unreadable {COLLECTED_INPUTS_FILE} in {state_dir} (ValidationError: "
    ) in resumed.output
    assert (
        "its collected inputs count as changed — invalidated: summarize, report"
    ) in resumed.output
    [warning] = [
        r for r in caplog.records if "treating its collected inputs as changed" in r.getMessage()
    ]
    assert warning.getMessage().startswith(f"step 'summarize': unreadable {COLLECTED_INPUTS_FILE}")
    assert warning.exc_info is not None
    assert "Step 'summarize': collected input" not in resumed.output
    assert "Step 'tally': collected input 'outcomes' from 'scan' changed" in resumed.output
    assert (run_dir / "report.txt").read_text(encoding="utf-8") == "report:a,b\n"
    assert _invocations(run_dir) == Counter(
        {"scan:a": 1, "scan:b": 2, "summarize": 2, "report": 2, "tally": 2}
    )
    # The delivery that re-ran the consumer replaced the unreadable record.
    replaced = read_collected_inputs_at(state_dir)
    assert replaced is not None
    assert replaced.inputs["outcomes"].outcomes_sha256 == (
        build_outcome_manifest(run_dir, "scan", expected_keys=["a", "b"]).outcomes_sha256
    )
    assert (state_dir / "status.yaml.stale").exists()
    assert _step_states(run_dir) == {
        "scan": "current",
        "summarize": "current",
        "report": "current",
        "tally": "current",
    }
