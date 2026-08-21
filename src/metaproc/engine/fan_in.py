"""Fan-in: deliver an upstream fan-out's per-item outcomes as one collection.

A consumer of a fan-out needs to know what happened to every item, including the ones
that failed. Without that it has to rediscover upstream state by walking directories,
which makes a missing artifact and a failed item indistinguishable, and makes partial
success look like corruption.

The manifest here is derived from durable per-item state, never stored as truth: it is
rebuilt on every read, so it cannot drift from the state it describes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from strif import atomic_output_file

from metaproc.io.state_io import read_status_at

OUTCOME_CONTRACT = "metaproc:FanInOutcomes/0.1"

# Terminal states an item can end in. `completed` is the only success; everything else
# is a terminal non-success the consumer may still want to see and account for.
_SUCCESS_STATES = frozenset({"completed", "cached"})


def collect_item_outcomes(
    run_dir: Path, upstream_step_id: str, expected_keys: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Read every item's terminal state for one fan-out step.

    ``expected_keys`` is the roster the step was supposed to cover. Reporting against it
    rather than against the task directories on disk is what makes a dropped item
    visible: an item that died upstream never creates a task directory here, so a
    collection over what arrived would call three of four items full coverage. The
    consumer needs to distinguish "succeeded", "failed", and "never got here", and only
    the expected set can express the third.

    Sorted by key so the manifest is stable across runs and diffable.
    """
    tasks_dir = run_dir / ".state" / "tasks" / upstream_step_id
    on_disk = {p.name for p in tasks_dir.iterdir() if p.is_dir()} if tasks_dir.is_dir() else set()
    keys = sorted(set(expected_keys) | on_disk) if expected_keys else sorted(on_disk)

    outcomes: list[dict[str, Any]] = []
    for key in keys:
        item_dir = tasks_dir / key
        if not item_dir.is_dir():
            # Expected by the roster and never started: the item died upstream.
            outcomes.append({"key": key, "state": "not_reached", "succeeded": False})
            continue
        try:
            status = read_status_at(item_dir)
        except (ValueError, TypeError, ValidationError):
            # A status this function cannot read is still an item the consumer needs
            # told about. Raising here would let one malformed record destroy the whole
            # collection, in the one function whose job is reporting partial failure.
            outcomes.append({"key": key, "state": "unreadable", "succeeded": False})
            continue
        if status is None:
            outcomes.append({"key": key, "state": "unknown", "succeeded": False})
            continue
        state = str(status.state)
        record: dict[str, Any] = {
            "key": key,
            "state": state,
            "succeeded": state in _SUCCESS_STATES,
        }
        error = getattr(status, "error", None)
        if error:
            record["error"] = str(error)
        # Pass softschema's vocabulary through unchanged rather than flattening it to
        # the sentence. A consumer routing work by owner needs to tell a missing output
        # from a refused invariant, and the string form cannot express that difference.
        failures = getattr(status, "output_failures", None) or []
        if failures:
            record["output_failures"] = [
                {
                    key: value
                    for key, value in (
                        ("output", failure.output),
                        ("kind", str(failure.kind)),
                        ("contract", failure.contract),
                        ("invariant", failure.invariant),
                        ("location", failure.location),
                    )
                    if value is not None
                }
                for failure in failures
            ]
        outcomes.append(record)
    return outcomes


def write_outcome_manifest(
    run_dir: Path,
    upstream_step_id: str,
    destination: Path,
    expected_keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build and write the fan-in manifest for one upstream step."""
    outcomes = collect_item_outcomes(run_dir, upstream_step_id, expected_keys)
    succeeded = sum(1 for o in outcomes if o["succeeded"])
    payload = {
        "fan_in_outcomes": {
            "schema": OUTCOME_CONTRACT,
            "upstream_step": upstream_step_id,
            "total": len(outcomes),
            "succeeded": succeeded,
            "failed": len(outcomes) - succeeded,
            "items": outcomes,
        }
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_file(destination) as tmp_path:
        tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return payload
