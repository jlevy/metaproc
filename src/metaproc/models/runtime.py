"""Runtime state models — .state/ directory records.

These models track execution state: what was launched (AttemptRecord),
current status (StatusRecord), validated results (ResultRecord), and
manual-step acknowledgments (ManualAckRecord).
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from metaproc.models.authored import StepStatus


class StepState(StrEnum):
    """Per-step state surfaced to operators by ``metaproc status``.

    Single source of truth for the five-state vocabulary:

    - ``current``: completed, outputs valid, fingerprint matches the
      current process definition.
    - ``stale``: completed, outputs valid, but the current step
      fingerprint differs from the recorded one (operator edited the
      runbook since the last completion).
    - ``invalidated``: a prior completion record was renamed ``.stale`` by
      ``--force`` or a fingerprint cascade — the step will rerun.
    - ``missing``: never started, or started and failed without a
      recorded completion.
    - ``in_flight``: actively running.
    """

    current = "current"
    stale = "stale"
    invalidated = "invalidated"
    missing = "missing"
    in_flight = "in_flight"


class StatusRecord(BaseModel):
    """Per-item harness-owned status — written to .state/status.yaml."""

    run_id: str
    step_id: str
    item: dict[str, str]
    state: StepStatus
    attempt: int = 1
    started_at: str = ""
    completed_at: str | None = None
    last_heartbeat_at: str | None = None
    error: str | None = None


class AttemptRecord(BaseModel):
    """Records what was launched — written to .state/attempt.yaml."""

    run_id: str
    step_id: str
    item: dict[str, str]
    params: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    runtime: dict[str, object] = Field(default_factory=dict)
    step_hash: str | None = None


class ResultRecord(BaseModel):
    """Validated outcome — written to .state/result.yaml after output check."""

    run_id: str
    step_id: str
    state: StepStatus
    validated: bool
    outputs: dict[str, str] = Field(default_factory=dict)
    published_at: str = ""
    step_hash: str | None = None


class ManualAckRecord(BaseModel):
    """Operator acknowledgment for a manual step — written to .state/manual-ack.yaml."""

    run_id: str
    step_id: str
    operator: str
    acknowledged_at: str
    note: str | None = None


# ── Generic map-reduce item models ────────────────────────────────


class MapItem(BaseModel):
    """One item in a fan-out source file.

    Has no hardcoded fields — all fields accepted via extra="allow".
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


class MapItemsFrontmatter(BaseModel):
    """Generic frontmatter for a fan-out source file."""

    items: list[MapItem] = Field(default_factory=list)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")


# ── Envelope wrappers ─────────────────────────────────────────

# ProcessEnvelope lives in metaproc.io.frontmatter to avoid circular imports.


# ── Terminal status registry ─────────────────────────────────────

_TERMINAL_STATUSES: set[str] = {"completed", "cached"}


def register_terminal_statuses(statuses: frozenset[str]) -> None:
    """Register additional terminal statuses for local/manual callers."""
    _TERMINAL_STATUSES.update(statuses)


def get_terminal_statuses() -> frozenset[str]:
    """Return the merged set of terminal statuses from all domains."""
    statuses = set(_TERMINAL_STATUSES)
    try:
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            get_plugin_registry,
        )
    except ImportError:
        return frozenset(statuses)
    statuses.update(get_plugin_registry().terminal_statuses)
    return frozenset(statuses)
