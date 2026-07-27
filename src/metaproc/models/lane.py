"""Execution-lane models — domain-neutral lane matrix expansion.

A *lane matrix* is a declarative description of which execution profiles,
replicas, and lane-local overrides a process should fan its domain items
across. Materializing the matrix yields :class:`ExecutionLane` rows; each
domain item × lane pair becomes a :class:`TaskInstance` that runpool
admission and trace metadata can address by ``lane_id``.

See ``docs/arch/arch-metaproc-core.md``
for the design.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LaneId = str
"""Stable materialized lane identifier.

Derived from ``execution_profile``, ``replica_index``, and a canonical
serialization of ``overrides``. Operators may pin a lane id explicitly via
``LaneSpec.lane_id``; otherwise it is computed by
:func:`derive_lane_id`.
"""

_LANE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
"""Safe lane id charset — same constraint as ``item_key`` so lane ids can
participate in filesystem paths without sanitization."""


class LaneSpec(BaseModel):
    """Authored lane row in a process ``lane_matrix``.

    The minimal authored form is just ``{execution_profile: <name>}``; the
    other fields are optional. Operators may supply an explicit ``lane_id``
    when stable cross-run identifiers are required.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    execution_profile: str
    replica_index: int = 0
    lane_id: LaneId | None = None
    lane_label: str | None = None
    artifact_namespace: str | None = None
    comparison_role: str | None = None
    overrides: dict[str, object] = Field(default_factory=dict)

    @field_validator("replica_index")
    @classmethod
    def _validate_replica_index(cls, value: int) -> int:
        if value < 0:
            raise ValueError("replica_index must be >= 0")
        return value

    @field_validator("lane_id")
    @classmethod
    def _validate_lane_id(cls, value: LaneId | None) -> LaneId | None:
        if value is None:
            return None
        if not _LANE_ID_RE.fullmatch(value):
            raise ValueError(f"lane_id must match {_LANE_ID_RE.pattern!r}; got {value!r}")
        return value


class LaneMatrix(BaseModel):
    """Declarative lane matrix authored alongside a process spec.

    ``lanes`` is the explicit list of lane specs that materialize. The
    shorthand fields ``profiles`` and ``replicas`` expand into ``lanes`` at
    validation time so simple ensembles ("Codex + Claude, two replicas each")
    do not require hand-listing every row.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    lanes: list[LaneSpec] = Field(default_factory=list)
    profiles: list[str] = Field(default_factory=list)
    replicas: int = 1

    @field_validator("replicas")
    @classmethod
    def _validate_replicas(cls, value: int) -> int:
        if value < 1:
            raise ValueError("replicas must be >= 1")
        return value

    @model_validator(mode="after")
    def _expand_shorthand(self) -> LaneMatrix:
        """Expand ``profiles`` + ``replicas`` into ``lanes``.

        The shorthand and explicit forms are additive: ``lanes`` rows are
        appended to whatever the shorthand expands to. Empty matrices (no
        lanes, no profiles) validate so callers can build a default matrix
        from a run-time profile.
        """
        if not self.profiles and not self.lanes:
            return self
        expanded: list[LaneSpec] = []
        for profile in self.profiles:
            for replica_index in range(self.replicas):
                expanded.append(
                    LaneSpec(
                        execution_profile=profile,
                        replica_index=replica_index,
                    )
                )
        expanded.extend(self.lanes)
        self.lanes = expanded
        # Clear shorthand so model_dump doesn't double-count on round-trip.
        self.profiles = []
        self.replicas = 1
        return self


class ExecutionLane(BaseModel):
    """A materialized lane: a stable row from a lane matrix.

    The ``lane_id`` is deterministic — re-materializing the same matrix
    against the same registry produces the same id, so downstream artifacts
    (trace events, runpool status rows, artifact paths) remain joinable
    across runs.

    ``adapter`` is optional because callers without profile-registry
    access (the runpool's degenerate single-profile synthesis) cannot
    fill it in; the lane materializer in
    :mod:`metaproc.engine.lane_expand` always populates it when a
    matrix is expanded against a registry.

    ``model_config.frozen`` blocks reassignment of the top-level
    fields — registered lane rows can't be accidentally swapped out
    by status or rollup code. Note: Pydantic's ``frozen`` does not
    recursively freeze nested containers, so a caller holding a
    reference to ``lane.overrides`` could still mutate the dict in
    place. Treat ``overrides`` as read-only by convention; the
    materializer copies it into the model so mutating the original
    spec doesn't affect the registered lane.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    lane_id: LaneId
    execution_profile: str
    adapter: str | None = None
    replica_index: int = 0
    lane_label: str | None = None
    artifact_namespace: str | None = None
    comparison_role: str | None = None
    overrides: dict[str, object] = Field(default_factory=dict)


class TaskInstance(BaseModel):
    """A concrete unit of work — one domain item routed through one lane."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    task_id: str
    step_id: str
    lane_id: LaneId
    item_key: str
    execution_profile: str
    replica_index: int = 0
    item_context: dict[str, str] = Field(default_factory=dict)


def derive_lane_id(
    *,
    execution_profile: str,
    replica_index: int = 0,
    overrides: dict[str, object] | None = None,
) -> LaneId:
    """Return the deterministic lane id for a materialized lane row.

    The id is filesystem-safe and stable: the same inputs always produce
    the same id, so trace events emitted from a lane in one run join
    cleanly against status rows or artifact paths in another. When
    ``overrides`` is empty and ``replica_index`` is 0, the id is just the
    profile name — the degenerate one-profile, one-replica case keeps the
    short familiar shape.
    """
    if not execution_profile:
        raise ValueError("execution_profile is required to derive a lane_id")
    overrides = overrides or {}
    suffix_parts: list[str] = []
    if replica_index > 0:
        suffix_parts.append(f"r{replica_index}")
    if overrides:
        canonical = json.dumps(overrides, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
        suffix_parts.append(f"o{digest}")
    base = _sanitize_lane_component(execution_profile)
    if not suffix_parts:
        return base
    return f"{base}-" + "-".join(suffix_parts)


def _sanitize_lane_component(value: str) -> str:
    """Coerce a free-form string into the lane-id charset.

    Only ``[A-Za-z0-9._-]`` is preserved; other characters become ``_``.
    The result is non-empty (callers pass non-empty profile names).
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def derive_task_id(*, step_id: str, lane_id: LaneId, item_key: str) -> str:
    """Return the canonical task-instance identifier.

    Format: ``<step_id>:<lane_id>:<item_key>``. Stable and joinable with
    trace ``step.id`` / ``lane_id`` / ``item.key`` attributes.
    """
    return f"{step_id}:{lane_id}:{item_key}"
