"""Pydantic model for .state/invocation.yaml — lifecycle record for one agent step.

Written in phases: pending → running → completed/failed/timeout.
See the standalone CLI spec for the full lifecycle description.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class InvocationRecord(BaseModel):
    """Full lifecycle record for one agent step invocation."""

    # Identity
    run_id: str
    step_id: str
    item: dict[str, str] = Field(default_factory=dict)

    # Launch — set just after Popen
    pid: int | None = None
    command: list[str] = Field(default_factory=list)
    adapter_type: str = ""
    model: str = ""
    log_path: str | None = None
    started_at: str | None = None
    timeout_s: int | None = None

    # State machine — updated at launch and at exit
    state: str = "pending"

    # Completion — set on exit (success or failure)
    completed_at: str | None = None
    exit_code: int | None = None
    duration_s: float | None = None

    # Error detail — set on failure only
    error: str | None = None
    error_type: str | None = None

    # Post-run metadata — set after exit
    validated: bool | None = None
    published_at: str | None = None

    # Resource usage — set on completion if available
    cost_usd: float | None = None
    total_tokens: int | None = None
    tool_uses: int | None = None
