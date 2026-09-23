"""The inputs a process scope binds to one value for its whole life.

An input declared ``on_change: new_run`` is bound when its scope is first entered:
``run-process`` binds the root scope's inputs when it creates the run, and the
orchestrator binds a composite child scope's when it first prepares that scope, after
``with:`` resolution. The record is ``input-bindings.yaml`` in the scope's ``.state/``
(``InputBindingsRecord``), keyed by the input's logical name and holding the value the
scope resolved. A scope whose process binds no input has no record.

Every later entry into the scope compares against that record before it writes
anything: a ``run-process`` resume before and again under the orchestrator lease, a
composite child scope before its plan is published, ``run-step`` and ``run-parallel``
before they touch task state. The comparison (``compare_input_bindings``) sorts each
recorded and each declared input into one of three outcomes:

- **changed**: a recorded binding whose value this launch resolves differently. The
  launch is refused (``input_binding_refusal``), whether or not the process still
  declares the input ``new_run``: a binding holds until it is released at its recorded
  value, so editing the spec cannot erase the promise.
- **released**: a recorded binding resolved at its recorded value by a process that no
  longer declares the input ``new_run``. Recorded as a launch-config change and dropped
  from the record.
- **adopted**: an input the process declares ``new_run`` that the record does not hold,
  because the run predates the declaration or the record. Bound at the value this launch
  resolves and recorded as a launch-config change; the run acquires no guarantee about
  launches before that one.

Only ``run-process`` and composite scope entry establish, release, and adopt bindings.
``run-step`` and ``run-parallel`` honor a record and change nothing, so a launch through
them never widens or narrows what the scope binds.

Values are compared after alias resolution, under the logical name, so a renamed
``param`` alias changes nothing. An input backed by ``RUNS_DIR`` compares across the two
Filestore mount aliases, as the run directory does. Comparison and rendering live here;
the commands layer turns a refusal into its error.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from metaproc.engine.pathing import normalize_filestore_runs_path
from metaproc.io.state_io import read_input_bindings
from metaproc.models.authored import ProcessInput, ProcessSpec
from metaproc.models.runtime import InputBinding


@dataclasses.dataclass(frozen=True, slots=True)
class InputBindingChanges:
    """How one launch's resolved values relate to a scope's recorded bindings."""

    changed: dict[str, tuple[str | None, str | None]]
    """Recorded bindings this launch resolves differently: ``name -> (recorded, current)``.
    Any entry refuses the launch."""

    released: dict[str, InputBinding]
    """Recorded bindings, resolved at their recorded value, that the process no longer
    declares ``new_run``."""

    adopted: dict[str, InputBinding]
    """Inputs the process declares ``new_run`` that the record does not hold, at the value
    this launch resolves."""

    @property
    def refuses(self) -> bool:
        return bool(self.changed)

    @property
    def rewrites(self) -> bool:
        """Whether applying the changes gives a record different from the recorded one."""
        return bool(self.released or self.adopted)


def declared_input_bindings(
    spec: ProcessSpec, variables: Mapping[str, str]
) -> dict[str, InputBinding]:
    """Return the bindings *spec* declares, at the values *variables* resolve.

    *variables* are the scope's resolved variables (``expand_process_vars``), so each
    logical input name holds its value; an unset optional input binds to ``None``.
    """
    return {
        name: InputBinding(on_change="new_run", value=_resolved_value(decl, name, variables))
        for name, decl in spec.new_run_inputs.items()
    }


def compare_input_bindings(
    recorded: Mapping[str, InputBinding],
    spec: ProcessSpec,
    variables: Mapping[str, str],
) -> InputBindingChanges:
    """Sort *recorded* and the bindings *spec* declares into changed, released, and adopted.

    A recorded binding is compared under its logical name against the value *variables*
    resolve for it, normalized as the input's declaration asks (``_resolved_value``),
    whether or not the process still declares the input at all.
    """
    declared = declared_input_bindings(spec, variables)
    changed: dict[str, tuple[str | None, str | None]] = {}
    released: dict[str, InputBinding] = {}
    for name, binding in recorded.items():
        decl = spec.inputs.get(name)
        current = _resolved_value(decl, name, variables)
        if _comparable(decl, binding.value) != _comparable(decl, current):
            changed[name] = (binding.value, current)
        elif name not in declared:
            released[name] = binding
    adopted = {name: binding for name, binding in declared.items() if name not in recorded}
    return InputBindingChanges(changed=changed, released=released, adopted=adopted)


def apply_input_binding_changes(
    recorded: Mapping[str, InputBinding], changes: InputBindingChanges
) -> dict[str, InputBinding]:
    """Return the bindings a scope holds once *changes* are applied to *recorded*.

    Only a launch with no changed binding gets this far; a changed one is refused.
    """
    if changes.refuses:
        raise ValueError("cannot apply input binding changes that refuse the launch")
    bindings = {name: binding for name, binding in recorded.items() if name not in changes.released}
    bindings.update(changes.adopted)
    return bindings


def recorded_input_bindings(scope_dir: Path) -> dict[str, InputBinding]:
    """Return the bindings *scope_dir* records, empty when it records none.

    A record that is present but cannot be read raises, as ``read_input_bindings``
    does; continuing would compare against a record that no longer says what the
    scope binds.
    """
    record = read_input_bindings(scope_dir)
    return {} if record is None else dict(record.bindings)


def input_binding_refusal(
    changes: InputBindingChanges,
    *,
    spec: ProcessSpec,
    record_path: Path,
    launch: str,
) -> str | None:
    """Return why *launch* is refused for its changed bindings, or ``None`` when none is.

    *launch* names the launch in the message's first words (``Resume``, ``run-step``,
    ``run-parallel``). The message names each changed input with its recorded and its
    new value, marks one the process no longer declares ``new_run``, and gives the two
    ways out: the recorded value, or a new ``RUN_ID``.
    """
    if not changes.changed:
        return None
    lines: list[str] = []
    for name, (recorded, current) in sorted(changes.changed.items()):
        note = (
            ""
            if name in spec.new_run_inputs
            else " (no longer declared `on_change: new_run`; a binding is released only at "
            "its recorded value)"
        )
        lines.append(
            f"  {name}: {describe_bound_value(recorded)} -> {describe_bound_value(current)}{note}"
        )
    count = len(changes.changed)
    plural = count != 1
    return (
        f"{launch} refused: this launch changes {count} input{'s' if plural else ''} bound "
        f"`on_change: new_run` in {record_path}:\n" + "\n".join(lines) + "\n"
        "A `new_run` input holds one value for the life of a run: the run's task state, "
        "results, and summaries all describe the recorded value. Resume with the recorded "
        f"value{'s' if plural else ''}, or start a new RUN_ID for the new "
        f"{'ones' if plural else 'one'}."
    )


def describe_bound_value(value: str | None) -> str:
    """Render a bound value for a message, marking an unset one."""
    return "<unset>" if value is None else repr(value)


def _resolved_value(
    decl: ProcessInput | None, name: str, variables: Mapping[str, str]
) -> str | None:
    """Return the value *variables* resolve for the input *name*.

    Resolution writes a ``param``-backed input's value under its logical name; the
    ``param`` alias is read only when the logical name is absent, which happens when
    the operator supplies the alias of an input the process no longer declares.
    """
    value = variables.get(name)
    if value is None and decl is not None and decl.param is not None:
        value = variables.get(decl.param)
    return value


def _comparable(decl: ProcessInput | None, value: str | None) -> str | None:
    """Return *value* as bindings compare it: a ``RUNS_DIR`` input across mount aliases."""
    if value is None or decl is None or decl.param != "RUNS_DIR":
        return value
    return normalize_filestore_runs_path(value)
