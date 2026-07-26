"""Neutral data carrier composing a Plan with its authored spec + composite children.

Lives in ``metaproc.models`` so engine and viz can both consume the
same shape without engine reaching into the projection package
(``metaproc.viz``). This is the same correction the
resource-observability spec required for the qualified-id contract:
shared structural types live in ``metaproc.models``.

The projection layer (``metaproc.viz.project``) re-exports
:class:`PlanBundle` so existing call sites compile unchanged. New code
should import from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan


@dataclass(frozen=True)
class PlanBundle:
    """A process resolved enough to project: plan + authored spec + source + body.

    ``children`` holds one entry per composite step whose ``step_id``
    appears in ``plan.steps``. Nested composites are expressed by each
    child bundle carrying its own ``children``.
    """

    plan: Plan
    spec: ProcessSpec
    source_path: str
    body_markdown: str = ""
    children: Mapping[str, PlanBundle] = field(default_factory=dict)
