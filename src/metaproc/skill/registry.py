"""Skill registry: discovers skills via the ``metaproc.skills`` entry-point group.

Each entry point should reference a callable (typically a module-level function)
that returns a :class:`~metaproc.skill.spec.SkillSpec` instance.

Example entry in ``pyproject.toml``::

    [project.entry-points."metaproc.skills"]
    metaproc = "metaproc.skill.builtin:metaproc_skill_spec"

Workflow plugins register their own skill via the same group
without metaproc needing to import them.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Iterator

from metaproc.skill.spec import SkillSpec

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "metaproc.skills"


def iter_skills() -> Iterator[SkillSpec]:
    """Yield all registered skill specs discovered via entry points."""
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        try:
            factory = ep.load()
            spec = factory() if callable(factory) else factory
            if isinstance(spec, SkillSpec):
                yield spec
            else:
                log.warning(
                    "Entry point %s returned %s, expected SkillSpec",
                    ep.name,
                    type(spec).__name__,
                )
        except Exception:
            log.exception("Failed to load skill entry point: %s", ep.name)


def get_skill(name: str) -> SkillSpec | None:
    """Return the skill spec with the given name, or None if not found."""
    for spec in iter_skills():
        if spec.name == name:
            return spec
    return None
