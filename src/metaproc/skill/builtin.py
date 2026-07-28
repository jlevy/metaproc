"""Built-in metaproc skill spec, registered via the ``metaproc.skills`` entry-point group.

The catalog callable generates a help-topic listing from
:class:`metaproc.docs.HelpTopics` so the skill file stays in sync with the
actual bundled docs without manual updates.
"""

from __future__ import annotations

from dataclasses import fields as dc_fields

from metaproc.docs import TOPIC_DESCRIPTIONS as _HELP_TOPIC_DESCRIPTIONS
from metaproc.docs import HelpTopics
from metaproc.skill.spec import SkillSpec


def _help_topic_catalog() -> str:
    """Generate a catalog section listing ``metaproc help`` topics.

    Sources the actual topic names from :class:`metaproc.docs.HelpTopics` fields
    so the list never goes stale.
    """

    lines: list[str] = []
    lines.append("## Help Topics")
    lines.append("")
    lines.append("Available via `metaproc help <topic>`:")
    lines.append("")

    # Use the dataclass fields to enumerate actual topics
    topic_names = [f.name for f in dc_fields(HelpTopics)]
    for name in sorted(topic_names):
        desc = _HELP_TOPIC_DESCRIPTIONS.get(name, "")
        if desc:
            lines.append(f"- **{name}** — {desc}")
        else:
            lines.append(f"- **{name}**")

    return "\n".join(lines)


def metaproc_skill_spec() -> SkillSpec:
    """Factory function returning the metaproc SkillSpec.

    Registered as the ``metaproc`` entry in the ``metaproc.skills``
    entry-point group.
    """
    return SkillSpec(
        name="metaproc",
        description=(
            "How to run, monitor, resume, and supervise any metaproc process. Use "
            "whenever launching/resuming `metaproc run-process`, running a step in "
            "isolation, or supervising a run. ALWAYS read the operator manual "
            "(`metaproc help operator`) first; this skill routes you to metaproc's "
            "built-in manuals (`metaproc help operator/developer/concepts`) and the "
            "monitoring commands instead of improvising ad-hoc bash."
        ),
        # Space-separated per the Agent Skills specification. `uv run metaproc`
        # invocations are not pre-approved: entries with embedded spaces cannot be
        # expressed in the space-separated form, so source checkouts prompt instead.
        allowed_tools="Bash(metaproc:*) Read",
        baseline_package="metaproc.skill.baselines",
        baseline_name="metaproc",
        catalog_fn=_help_topic_catalog,
    )
