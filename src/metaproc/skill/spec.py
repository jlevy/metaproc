"""SkillSpec model: defines a skill's metadata, baseline, and optional catalog."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from metaproc.resource_docs import load_resource_md

if TYPE_CHECKING:
    pass


class SkillSpec(BaseModel):
    """Specification for a generated skill artifact.

    Attributes:
        name: Unique skill identifier (used as directory name and CLI argument).
        description: What+when activation text shown in skill listings and frontmatter.
        allowed_tools: Optional tool-permission string for the SKILL.md frontmatter.
        baseline_package: Python package path containing the baseline .md file.
        baseline_name: Filename (without .md) of the baseline within the package.
        catalog_fn: Optional callable returning a dynamic catalog section (markdown).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    allowed_tools: str | None = None
    baseline_package: str
    baseline_name: str
    catalog_fn: Callable[[], str] | None = None

    def load_baseline(self) -> str:
        """Load the baseline markdown body from package resources."""

        return load_resource_md(self.baseline_package, self.baseline_name)
