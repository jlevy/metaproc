"""Overrides document I/O -- run-scoped operator escape hatch.

Manages ``$RUN_DIR/.state/overrides.yaml``, which records step-level
operator overrides that let the dependency resolver treat a step as
satisfied even when its terminal state is not ``succeeded``.

Schema token: ``metaproc:OverridesDocument/0.1``
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from frontmatter_format import read_yaml_file, to_yaml_string
from pydantic import BaseModel, ConfigDict, Field
from strif import atomic_output_file

from metaproc.paths import OVERRIDES_FILE, STATE_DIR

log = logging.getLogger(__name__)

SCHEMA_TOKEN = "metaproc:OverridesDocument/0.1"


class OverrideEntry(BaseModel):
    """A single override entry."""

    model_config = ConfigDict(populate_by_name=True)

    step: str
    action: str = "satisfied"
    item: str | None = None
    for_downstream: list[str] | None = None
    by: str
    at: str
    note: str = ""


class OverridesDocument(BaseModel):
    """Schema for ``$RUN_DIR/.state/overrides.yaml``."""

    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default=SCHEMA_TOKEN, alias="schema")
    run_id: str
    entries: list[OverrideEntry] = []


def _overrides_path(run_dir: Path) -> Path:
    return run_dir / STATE_DIR / OVERRIDES_FILE


def _entry_key(entry: OverrideEntry) -> tuple[str, str | None, tuple[str, ...] | None]:
    """Identity tuple for dedup: (step, item, sorted for_downstream or None)."""
    fd = tuple(sorted(entry.for_downstream)) if entry.for_downstream else None
    return (entry.step, entry.item, fd)


def read_overrides(run_dir: Path) -> OverridesDocument | None:
    """Read the overrides document, returning None if the file is absent."""
    path = _overrides_path(run_dir)
    if not path.exists():
        return None
    raw = read_yaml_file(path)
    return OverridesDocument.model_validate(raw)


def _write_overrides(run_dir: Path, doc: OverridesDocument) -> None:
    """Atomically write the overrides document."""
    path = _overrides_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = doc.model_dump(by_alias=True, exclude_none=True)
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(to_yaml_string(data))


def upsert_override(
    run_dir: Path,
    *,
    step: str,
    action: str = "satisfied",
    item: str | None = None,
    for_downstream: list[str] | None = None,
    by: str,
    note: str,
) -> None:
    """Add or update an override entry, idempotent on (step, item, for_downstream).

    If an entry with the same identity tuple exists, its ``at`` and ``note``
    fields are updated. Otherwise a new entry is appended.
    """
    run_id = run_dir.name
    doc = read_overrides(run_dir) or OverridesDocument(run_id=run_id)
    now = datetime.now(tz=UTC).isoformat()

    new_entry = OverrideEntry(
        step=step,
        action=action,
        item=item,
        for_downstream=for_downstream,
        by=by,
        at=now,
        note=note,
    )
    new_key = _entry_key(new_entry)

    replaced = False
    for i, existing in enumerate(doc.entries):
        if _entry_key(existing) == new_key:
            doc.entries[i] = new_entry
            replaced = True
            break

    if not replaced:
        doc.entries.append(new_entry)

    _write_overrides(run_dir, doc)
    log.info("Override upserted: step=%s item=%s action=%s", step, item, action)


def clear_override(
    run_dir: Path,
    *,
    step: str,
    item: str | None = None,
    for_downstream: list[str] | None = None,
) -> bool:
    """Remove an override entry matching the identity tuple.

    Returns True if an entry was removed, False if no match found.
    """
    doc = read_overrides(run_dir)
    if doc is None:
        return False

    target_fd = tuple(sorted(for_downstream)) if for_downstream else None
    target_key = (step, item, target_fd)

    original_len = len(doc.entries)
    doc.entries = [e for e in doc.entries if _entry_key(e) != target_key]

    if len(doc.entries) == original_len:
        return False

    _write_overrides(run_dir, doc)
    log.info("Override cleared: step=%s item=%s", step, item)
    return True


def overrides_for_step(doc: OverridesDocument, step: str) -> list[OverrideEntry]:
    """Return all override entries for the given step."""
    return [e for e in doc.entries if e.step == step]


def is_step_overridden(doc: OverridesDocument, step: str, downstream_id: str) -> bool:
    """Check if a step is overridden for a specific downstream.

    Returns True iff there is a ``satisfied`` entry for *step* whose
    ``for_downstream`` is None (all downstreams) or contains *downstream_id*.
    """
    for entry in doc.entries:
        if entry.step != step:
            continue
        if entry.action != "satisfied":
            continue
        if entry.for_downstream is None or downstream_id in entry.for_downstream:
            return True
    return False


def is_step_satisfied_universally(doc: OverridesDocument, step: str) -> bool:
    """Check if a step has a universal ``satisfied`` override (no downstream filter).

    Returns True iff there is a ``satisfied`` entry for *step* with
    ``for_downstream=None`` — i.e. the operator marked the step satisfied for
    every downstream consumer. Used by the run-process skip-step decision so
    that overrides apply on whole-DAG resume, not only on ``--from`` resume.
    """
    for entry in doc.entries:
        if entry.step != step:
            continue
        if entry.action != "satisfied":
            continue
        if entry.for_downstream is None:
            return True
    return False
