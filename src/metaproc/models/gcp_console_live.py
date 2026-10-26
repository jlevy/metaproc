"""The atomic, bounded monitoring snapshot shared by GCP console writers and readers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LIVE_SNAPSHOT_VERSION = 1
MAX_LIVE_OBJECT_BYTES = 4 * 1024 * 1024
MAX_TAIL_BYTES = 256 * 1024
MAX_RUN_STATUS_BYTES = 64 * 1024


class WatchedRunSnapshot(BaseModel):
    path: str
    text: str = ""
    updated_at: str | None = None


class ConsoleLiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_: Literal["metaproc:ConsoleLiveSnapshot/0.1"] = Field(
        default="metaproc:ConsoleLiveSnapshot/0.1", alias="schema"
    )
    snapshot_version: Literal[1] = LIVE_SNAPSHOT_VERSION
    console_path: str
    argv: list[str]
    host: str
    started_at: str
    updated_at: str
    state: Literal["running", "exited"]
    exit_code: int | None = None
    console_bytes: int = Field(ge=0)
    console_complete_bytes: int = Field(ge=0)
    console_lines: int = Field(ge=0)
    console_tail: str
    tail_truncated: bool
    tail_start_partial: bool
    tail_byte_limit: int = MAX_TAIL_BYTES
    watch: list[str] = Field(default_factory=list)
    watch_status: dict[str, WatchedRunSnapshot] = Field(default_factory=dict)
