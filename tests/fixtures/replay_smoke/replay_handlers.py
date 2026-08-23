"""Handlers for the replay-smoke fixture.

Each stage writes one JSON record per item. The two declared failures use different
mechanisms on purpose: a raise is an operational failure the engine records with no
structured contract detail, while returning without the declared output is a contract
failure that output validation catches and retries. The replay harness has to map both
faithfully.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from metaproc.models.authored import ProcessStep


def _out_path(step: ProcessStep, variables: dict[str, str], name: str = "out") -> Path:
    """The step's output path with the item's own bindings applied.

    A fan-out handler receives the authored template; the engine resolves per-item
    paths for validation but hands the step through as written, so the handler applies
    its bindings itself.
    """
    spec = step.outputs.get(name)
    if spec is None or not spec.path:
        msg = f"handler expects a declared {name!r} output path"
        raise RuntimeError(msg)
    path = spec.path
    for key, value in variables.items():
        path = path.replace("{{" + key + "}}", value)
    return Path(path)


def _run_stage(variables: dict[str, str], step: ProcessStep, stage: str) -> None:
    item = variables["item"]
    fail_in = variables.get("fail_in", "")

    if fail_in == stage:
        msg = f"{item}: deliberate failure in {stage}, declared by the roster"
        raise RuntimeError(msg)
    if fail_in == f"silent-in-{stage}":
        # Return as though it worked, without writing the declared output. Output
        # validation is what catches this; the handler's own exit status cannot. The
        # invocation log is durable evidence of how many times the engine really
        # called this handler, which the status record's attempt field undercounts.
        log = _out_path(step, variables).parent / "invocations.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as handle:
            handle.write(f"{stage}\n")
        return

    out = _out_path(step, variables)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"item": item, "stage": stage}) + "\n")


def stage_a(variables: dict[str, str], step: ProcessStep) -> None:
    _run_stage(variables, step, "stage-a")


def stage_b(variables: dict[str, str], step: ProcessStep) -> None:
    _run_stage(variables, step, "stage-b")


def stage_c(variables: dict[str, str], step: ProcessStep) -> None:
    _run_stage(variables, step, "stage-c")


def review(variables: dict[str, str], step: ProcessStep) -> None:
    """Fan-in: read the collected outcomes and write a verdict."""
    collected = step.inputs.get("outcomes")
    if collected is None or not collected.path:
        msg = "review expects a resolved 'outcomes' input path"
        raise RuntimeError(msg)
    with Path(collected.path).open() as handle:
        manifest = yaml.safe_load(handle)["fan_in_outcomes"]

    verdict = {
        "total": manifest["total"],
        "succeeded": manifest["succeeded"],
        "failed": manifest["failed"],
        "outcomes": {o["key"]: o["state"] for o in manifest["items"]},
    }
    out = _out_path(step, variables)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, indent=1) + "\n")
