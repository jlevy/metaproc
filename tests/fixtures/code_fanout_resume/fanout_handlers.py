"""Handler for the standalone code fan-out resume fixture.

The step writes one JSON record per item and appends to a shared invocation log, so a
resume test can distinguish a reused task from a rerun one by counting lines rather than
by inspecting engine state.
"""

from __future__ import annotations

import json
from pathlib import Path

from metaproc.models.authored import ProcessStep


def _out_path(step: ProcessStep, variables: dict[str, str]) -> Path:
    """The step's declared output path with the item's own bindings applied."""
    spec = step.outputs.get("out")
    if spec is None or not spec.path:
        msg = "handler expects a declared 'out' output path"
        raise RuntimeError(msg)
    path = spec.path
    for key, value in variables.items():
        path = path.replace("{{" + key + "}}", value)
    return Path(path)


def project(variables: dict[str, str], step: ProcessStep) -> None:
    item = variables["item"]
    out = _out_path(step, variables)
    out.parent.mkdir(parents=True, exist_ok=True)

    log = out.parent.parent / "invocations.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as handle:
        handle.write(f"{item}\n")

    out.write_text(json.dumps({"item": item}) + "\n")
