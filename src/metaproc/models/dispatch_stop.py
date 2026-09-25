"""The record a mapped step leaves when a spend cap or a breaker stops its dispatch."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

DispatchStopReason = Literal["spend-cap", "failure-rate"]


class DispatchStop(BaseModel):
    """Why a mapped step stopped starting items, with the numbers that decided it.

    ``process-status.yaml`` carries it as the step's ``stopped`` block and, for the
    scope, a top-level ``stopped`` list. The step and the scope still read ``failed``,
    so every reader that knows only the existing states treats the run as unfinished
    and resumable; ``label`` is the operator-facing name for the stop.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    reason: DispatchStopReason
    phase: Literal["pre-dispatch", "in-run"]
    step_id: str
    not_dispatched: int = Field(ge=0)
    message: str
    stopped_at: str
    cap_usd: float | None = None
    measured_usd: float | None = None
    worst_case_usd: float | None = None
    actionable_items: int | None = None
    max_attempts: int | None = None
    per_item_usd: float | None = None
    finished: int | None = None
    failed: int | None = None
    max_failure_fraction: float | None = None
    min_finished: int | None = None

    @property
    def label(self) -> str:
        """``budget-stopped`` for the spend cap, ``breaker-stopped`` for the breaker."""
        return "budget-stopped" if self.reason == "spend-cap" else "breaker-stopped"


__all__ = ["DispatchStop", "DispatchStopReason"]
