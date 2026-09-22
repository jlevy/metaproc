"""Collected inputs: the fan-in documents a ``collect:`` consumer is handed, and reuse.

A step that declares a ``collect:`` input is handed a fan-in document built from the
collected mapped step's durable per-item state (``metaproc.engine.fan_in``). When a
resume finishes an item that had failed, that document changes while the consumer's
fingerprint does not, so the fingerprint alone would reuse work computed over outcomes
that no longer hold.

This module builds a consumer's documents, records the digest of each one it was
handed in ``collected-inputs.yaml``, and reports which documents changed since. The
one digest is of the outcome projection (the upstream step, the totals, and each
item's key, state and success); error wording and attempt log paths are not part of
it. The orchestrator owns the invalidation a change triggers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ruamel.yaml import YAMLError

from metaproc.engine.fan_in import OutcomeManifest, build_outcome_manifest
from metaproc.engine.placeholders import resolve_templates
from metaproc.io.state_io import read_collected_inputs_at, write_collected_inputs_at
from metaproc.models.plan import ResolvedStep
from metaproc.models.runtime import CollectedInput, CollectedInputsRecord
from metaproc.paths import COLLECTED_INPUTS_FILE, STATE_DIR, TASKS_SUBDIR

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectedDocument:
    """One ``collect:`` input's fan-in document, built but not yet delivered."""

    input_name: str
    upstream_step: str
    path: Path
    manifest: OutcomeManifest


def _record_dir(run_dir: Path, step_id: str) -> Path:
    """The step's task state directory, ``<run>/.state/tasks/<step_id>/``, which holds
    its ``collected-inputs.yaml``."""
    return run_dir / STATE_DIR / TASKS_SUBDIR / step_id


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


def collected_documents(
    target: ResolvedStep,
    *,
    step_map: Mapping[str, ResolvedStep] | None,
    chains_by_member: Mapping[str, list[str]] | None,
    variables: dict[str, str],
    run_dir: Path,
) -> list[CollectedDocument]:
    """Build each of *target*'s ``collect:`` documents from durable per-item state.

    Writes nothing. The same call serves delivery, just before the consumer runs, and
    the level walk's reuse decision, which compares what the consumer would be handed
    now against what it was last handed.
    """
    documents: list[CollectedDocument] = []
    for input_name, io_spec in target.inputs.items():
        if not io_spec.collect or not io_spec.path:
            continue
        # The expected roster comes from the collected step's own resolved fan-out, so
        # an item that died before reaching it is still reported rather than absent.
        collected_step = (step_map or {}).get(io_spec.collect)
        expected_keys = None
        if collected_step is not None and collected_step.fan_out is not None:
            bind = collected_step.fan_out.bind
            expected_keys = [
                str(item[bind]) for item in collected_step.fan_out.items if bind in item
            ]
        # The chain feeding the collected step, so an item that never arrived can be
        # reported with where it stopped rather than as a bare absence.
        upstream_chain: list[str] = []
        for candidate in (chains_by_member or {}).get(io_spec.collect, []):
            upstream_chain.append(candidate)
            if candidate == io_spec.collect:
                break
        documents.append(
            CollectedDocument(
                input_name=input_name,
                upstream_step=io_spec.collect,
                path=Path(resolve_templates(io_spec.path, variables)),
                manifest=build_outcome_manifest(
                    run_dir, io_spec.collect, expected_keys, upstream_chain
                ),
            )
        )
    return documents


def read_collected_inputs(run_dir: Path, step_id: str) -> CollectedInputsRecord | None:
    """Read a step's ``collected-inputs.yaml``; an unreadable record counts as absent.

    A record that fails validation, including one written in an earlier shape, is
    logged and treated as absent, so it is never compared.
    """
    state_dir = _record_dir(run_dir, step_id)
    try:
        return read_collected_inputs_at(state_dir)
    except (YAMLError, ValueError) as exc:
        log.warning(
            "step %r: unreadable %s in %s (%s); its collected inputs cannot be compared",
            step_id,
            COLLECTED_INPUTS_FILE,
            state_dir,
            exc,
        )
        return None


def record_collected_inputs(
    run_dir: Path,
    *,
    run_id: str,
    step_id: str,
    documents: Sequence[CollectedDocument],
) -> None:
    """Record the digests of the documents just delivered to *step_id*.

    Recorded at delivery rather than at completion. A composite consumer's child
    steps complete one by one, so a child can consume the document while a sibling
    later fails the composite, and a scalar composite has no per-task completion
    record at all. The document a step was last handed is what its reusable work
    was computed over, whether or not the step then completed. An unchanged record
    for the same run is left as it is.
    """
    inputs = {
        document.input_name: CollectedInput(
            upstream_step=document.upstream_step,
            path=str(document.path),
            outcomes_sha256=document.manifest.outcomes_sha256,
        )
        for document in documents
    }
    prior = read_collected_inputs(run_dir, step_id)
    if prior is not None and prior.run_id == run_id and prior.inputs == inputs:
        return
    write_collected_inputs_at(
        _record_dir(run_dir, step_id),
        CollectedInputsRecord(
            run_id=run_id,
            step_id=step_id,
            recorded_at=_now_iso(),
            inputs=inputs,
        ),
    )


def changed_collected_inputs(
    run_dir: Path,
    step: ResolvedStep,
    *,
    step_map: Mapping[str, ResolvedStep],
    chains_by_member: Mapping[str, list[str]],
    variables: dict[str, str],
) -> list[CollectedDocument]:
    """The ``collect:`` documents *step* was last handed that durable per-item state
    no longer matches.

    A fan-in document changes when a mapped item's outcome changes, typically when a
    resume completes an item that had failed. The consumer's fingerprint does not
    move, so without this check its earlier completion, computed over the old
    outcomes, would be reused along with everything downstream of it.

    Compares outcome digests, never mtimes, bytes, or error wording: each item's key,
    state and success plus the totals, so an item that fails again with a different
    message or a new attempt log is not a change while an item that completes is.
    Returns an empty list when the step declares no ``collect:`` input, when it has
    no readable record (it last ran before the record existed, or the record fails
    validation), or when every recorded digest matches. An input the record does not
    name is not compared.
    """
    if not any(spec.collect and spec.path for spec in step.inputs.values()):
        return []
    recorded = read_collected_inputs(run_dir, step.step_id)
    if recorded is None:
        return []
    documents = collected_documents(
        step,
        step_map=step_map,
        chains_by_member=chains_by_member,
        variables=variables,
        run_dir=run_dir,
    )
    changed: list[CollectedDocument] = []
    for document in documents:
        prior = recorded.inputs.get(document.input_name)
        if prior is not None and prior.outcomes_sha256 != document.manifest.outcomes_sha256:
            changed.append(document)
    return changed
