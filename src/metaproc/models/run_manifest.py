"""RunManifest — Pydantic schema for scaling-validation run manifests.

The master plan (`plan-2026-04-17-incremental-mining-predict-scaling-validation.md`
§ Run manifest) specifies that every validation run records a manifest
YAML before launch. This module owns the canonical schema so evidence
stays consistent across phases. See also the parallel-cleanup handoff,
Item 4.

Round-trip contract: ``RunManifest.from_yaml_file(path)`` parses an
operator-authored manifest, validates it, and ``.to_yaml(path)`` writes
a canonical form with keys in a stable order. Operators can safely
load → save to normalize their existing files.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from metaproc.io import read_yaml_file, to_yaml_string, write_yaml_file

CostTier = Literal[
    "free-vertex",
    "free-vertex-maas",
    "paid-anthropic",
    "paid-openai",
]

Scoreboard = Literal["plug-compatible", "best-tuned"]

RuntimeArm = Literal[
    "canonical",
    "gemini-cli",
    "claude-code",
]

StageClass = Literal[
    "smoke",
    "baseline",
    "bakeoff",
    "topology",
    "concurrency",
    "dual-model",
]


class RunManifest(BaseModel):
    """Canonical schema for a scaling-validation run manifest.

    Fields mirror the plan's § Run manifest enumeration exactly — any
    addition here needs a matching addition in the plan (and evidence
    for why the existing set is insufficient).
    """

    run_id: str = Field(..., description="Unique ID for this run (matches RUNS_DIR subdir).")
    stage_class: StageClass
    dataset: str = Field(..., description="Dataset slug: smoke-us-traded-20, tech-mix-100, …")

    git_sha: str = Field(..., description="Full git SHA of the code dispatched.")
    image: str = Field(
        ...,
        description=(
            "Container image — either a floating tag (:latest) or a "
            "sha256:… digest. Digests are preferred; see scaling handoff's "
            "per-stage pre-launch gate for rationale."
        ),
    )

    models: list[str] = Field(
        ...,
        description=(
            "Ordered model list: single model for solo baselines, "
            "pair for dual-model topology runs."
        ),
        min_length=1,
    )
    cost_tier: CostTier
    scoreboard: Scoreboard
    runtime_arm: RuntimeArm

    num_workers: int = Field(..., ge=1)
    max_concurrency: int = Field(..., ge=1)

    launch_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit command-line flags passed to metaproc run-process, "
            "including --no-spot and --max-duration. Prefer explicit flags "
            "over shell defaults for unambiguous retrospectives."
        ),
    )

    notes: str | None = Field(
        default=None,
        description="Free-form operator note; tag qa-cascade: known runs here.",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> RunManifest:
        """Load and validate a manifest from a YAML file."""
        path = Path(path)
        data = read_yaml_file(path) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a YAML mapping at the top level")
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path | None = None) -> str:
        """Return YAML text in canonical form; optionally write to ``path`` (atomic)."""
        data = self.model_dump(mode="json", exclude_none=True)
        ordered = {k: data[k] for k in _CANONICAL_FIELD_ORDER if k in data}
        # Any extras land at the end — though extra="forbid" means this is
        # a no-op unless the model gains fields.
        for k, v in data.items():
            if k not in ordered:
                ordered[k] = v
        if path is not None:
            write_yaml_file(ordered, Path(path))
        return to_yaml_string(ordered)


_CANONICAL_FIELD_ORDER: tuple[str, ...] = (
    "run_id",
    "stage_class",
    "dataset",
    "git_sha",
    "image",
    "models",
    "cost_tier",
    "scoreboard",
    "runtime_arm",
    "num_workers",
    "max_concurrency",
    "launch_flags",
    "notes",
)
