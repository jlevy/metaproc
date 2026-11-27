"""Resume reuse of mapped code items follows what each item read, not only its key.

A step fingerprint describes the step and leaves runtime-discovered items out, so an
item whose input file or roster row changed under the same key used to be reused with
output computed from the old content. Each completed item now records the digests of
its bound roster fields and declared input files, and a resume re-runs exactly the
items whose digests differ.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from pathlib import Path

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.engine.dep_state import changed_item_inputs, fingerprint_item_inputs
from metaproc.io import read_yaml_file, to_yaml_string
from metaproc.io.state_io import read_result_at
from metaproc.models.authored import IOSpec
from metaproc.models.plan import FanOut, ResolvedStep
from metaproc.models.runtime import ITEM_INPUT_ABSENT, ITEM_INPUT_DIRECTORY
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

_ITEMS = ["alfa", "brvo", "chrl", "dlta", "echo"]

_HANDLERS = '''\
"""Handlers that copy each item's input, logging every invocation."""

from __future__ import annotations

from pathlib import Path


def _log(variables: dict[str, str], name: str) -> None:
    with (Path(variables["RUNS_DIR"]) / "invocations.log").open("a") as fh:
        fh.write(f"{name}:{variables['item']}\\n")


def _path(template: str, variables: dict[str, str]) -> Path:
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return Path(template)


def upper(variables: dict[str, str], step: object) -> None:
    _log(variables, "upper")
    text = _path(step.inputs["text"].path, variables).read_text()
    out = _path(step.outputs["out"].path, variables)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{variables['lang']}:{text.upper()}")


def count(variables: dict[str, str], step: object) -> None:
    _log(variables, "count")
    text = _path(step.inputs["upper"].path, variables).read_text()
    out = _path(step.outputs["out"].path, variables)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{len(text)}\\n")
'''

_PROCESS = """\
---
process:
  name: item-digests
  inputs:
    PROCESS_DIR: { param: PROCESS_DIR, as: string }
  deps:
    roster: { path: ./roster.md, as: path }
  steps:
    - id: upper
      mode: code
      handler: "handlers.py:upper"
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, lang]
        key: "{{item}}"
      inputs:
        text: { path: "{{PROCESS_DIR}}/inputs/{{item}}.txt", kind: file }
        roster: { path: "{{PROCESS_DIR}}/roster.md", kind: file }
      outputs:
        out: { path: "{{run.dir}}/upper/{{item}}.txt", kind: file }
---
# Item Digests
"""

_CHAIN_PROCESS = """\
---
process:
  name: item-digests
  inputs:
    PROCESS_DIR: { param: PROCESS_DIR, as: string }
  deps:
    roster: { path: ./roster.md, as: path }
  steps:
    - id: upper
      mode: code
      handler: "handlers.py:upper"
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, lang]
        key: "{{item}}"
      inputs:
        text: { path: "{{PROCESS_DIR}}/inputs/{{item}}.txt", kind: file }
      outputs:
        out: { path: "{{run.dir}}/upper/{{item}}.txt", kind: file }
    - id: count
      mode: code
      handler: "handlers.py:count"
      needs: [upper]
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, lang]
        key: "{{item}}"
        align: same_key
      inputs:
        upper: { ref: upper.out }
      outputs:
        out: { path: "{{run.dir}}/count/{{item}}.txt", kind: file }
---
# Item Digests Chain
"""


def _roster(rows: dict[str, str]) -> str:
    items = "".join(f"    - item: {item}\n      lang: {lang}\n" for item, lang in rows.items())
    return (
        "---\nprogress:\n  schema: metaproc:ProgressSpec/0.1\n  process: item-digests\n"
        f"  items:\n{items}---\n# Roster\n"
    )


class _Run:
    def __init__(self, tmp_path: Path, process: str = _PROCESS) -> None:
        self.process_dir = tmp_path / "process"
        (self.process_dir / "inputs").mkdir(parents=True)
        (self.process_dir / "handlers.py").write_text(_HANDLERS)
        self.write_roster(dict.fromkeys(_ITEMS, "en"))
        for item in _ITEMS:
            self.write_input(item, f"text of {item}\n")
        self.process_path = self.process_dir / "item-digests.process.md"
        self.process_path.write_text(textwrap.dedent(process))
        self.runs_dir = tmp_path / "runs"
        self.runs_dir.mkdir()
        self.run_dir = self.runs_dir / "r1"
        self.output = ""

    def write_roster(self, rows: dict[str, str]) -> None:
        (self.process_dir / "roster.md").write_text(_roster(rows))

    def write_input(self, item: str, text: str) -> None:
        (self.process_dir / "inputs" / f"{item}.txt").write_text(text)

    def launch(self) -> Counter[str]:
        """Run the process and return the invocations this launch made."""
        log = self.runs_dir / "invocations.log"
        before = log.read_text().splitlines() if log.exists() else []
        result = CliRunner().invoke(
            app,
            [
                "run-process",
                str(self.process_path),
                "--var",
                f"RUNS_DIR={self.runs_dir}",
                "--var",
                "RUN_ID=r1",
                "--var",
                f"PROCESS_DIR={self.process_dir}",
            ],
        )
        assert result.exit_code == 0, result.output
        self.output = result.output
        return Counter(log.read_text().splitlines()[len(before) :])

    def task_dir(self, step: str, item: str) -> Path:
        return self.run_dir / STATE_DIR / TASKS_SUBDIR / step / item


def test_resume_reruns_only_the_item_whose_input_file_changed(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    assert run.launch() == Counter(f"upper:{item}" for item in _ITEMS)

    result = read_result_at(run.task_dir("upper", "brvo"))
    assert result is not None and result.item_inputs is not None
    # The fan-out source is the item's row, so it is not hashed as an input.
    assert set(result.item_inputs.inputs) == {"text"}

    assert run.launch() == Counter(), "an unchanged resume must reuse every item"

    run.write_input("brvo", "new text of brvo\n")
    assert run.launch() == Counter({"upper:brvo": 1})
    assert "item 'brvo': input 'text' changed since it completed; re-running" in run.output
    assert (run.run_dir / "upper" / "brvo.txt").read_text() == "en:NEW TEXT OF BRVO\n"

    assert run.launch() == Counter(), "the re-run recorded the new digest"


def test_resume_reruns_an_item_whose_roster_fields_changed(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    run.launch()

    run.write_roster({**dict.fromkeys(_ITEMS, "en"), "dlta": "fr"})
    assert run.launch() == Counter({"upper:dlta": 1})
    assert "item 'dlta': roster fields changed" in run.output


def test_a_new_roster_row_does_not_rerun_existing_items(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    run.launch()

    run.write_input("fxtr", "text of fxtr\n")
    run.write_roster(dict.fromkeys([*_ITEMS, "fxtr"], "en"))
    assert run.launch() == Counter({"upper:fxtr": 1})


def test_a_result_without_digests_keeps_key_based_reuse(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    run.launch()

    # A run completed before this release has no item_inputs in its results.
    result_path = run.task_dir("upper", "chrl") / "result.yaml"
    raw = read_yaml_file(result_path)
    assert isinstance(raw, dict)
    raw.pop("item_inputs")
    result_path.write_text(to_yaml_string(raw))

    run.write_input("chrl", "changed\n")
    assert run.launch() == Counter()


def test_a_changed_chain_input_reruns_that_item_down_the_chain(tmp_path: Path) -> None:
    run = _Run(tmp_path, process=_CHAIN_PROCESS)
    first = run.launch()
    assert first == Counter([*(f"upper:{i}" for i in _ITEMS), *(f"count:{i}" for i in _ITEMS)])

    # A longer text changes upper's output, which is count's input for that item.
    run.write_input("echo", "much longer text of echo\n")
    assert run.launch() == Counter({"upper:echo": 1, "count:echo": 1})

    # Same length, different case: upper's output for the item is byte-identical after
    # it re-runs, so count, which reads only that output, is reused.
    run.write_input("echo", "MUCH LONGER TEXT OF ECHO\n")
    assert run.launch() == Counter({"upper:echo": 1})


def test_item_digest_parts(tmp_path: Path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("a\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    step = ResolvedStep(
        step_id="s",
        mode="code",
        fan_out=FanOut(
            over="deps.roster",
            bind="item",
            bind_fields=["item", "lang"],
            source=str(tmp_path / "roster.md"),
        ),
        inputs={
            "text": IOSpec(path=str(tmp_path / "{{item}}.txt"), kind="file"),
            "tree": IOSpec(path=str(tree), kind="directory"),
            "extra": IOSpec(path=str(tmp_path / "missing.txt"), optional=True),
            "roster": IOSpec(path=str(tmp_path / "roster.md")),
            "same": IOSpec(path=str(tmp_path / "out" / "{{item}}.txt")),
            "outcomes": IOSpec(path=str(tmp_path / "o.yaml"), collect="upstream"),
            "events": IOSpec(path=str(tmp_path / "e.jsonl"), kind="stream"),
        },
        outputs={"same": IOSpec(path=str(tmp_path / "out" / "{{item}}.txt"))},
    )
    first = fingerprint_item_inputs(step, {"item": "a", "lang": "en", "RUN_ID": "r1"})
    assert first.inputs["tree"] == ITEM_INPUT_DIRECTORY
    assert first.inputs["extra"] == ITEM_INPUT_ABSENT
    assert set(first.inputs) == {"text", "tree", "extra"}

    # Run variables other than bound fields are not part of the item.
    assert fingerprint_item_inputs(step, {"item": "a", "lang": "en", "RUN_ID": "r2"}) == first

    text.write_text("b\n")
    second = fingerprint_item_inputs(step, {"item": "a", "lang": "de"})
    assert changed_item_inputs(first, second) == ["roster fields", "input 'text'"]
