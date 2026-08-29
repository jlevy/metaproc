"""Built-in metaproc skill spec, registered via the ``metaproc.skills`` entry-point group.

The catalog callable generates a help-topic listing from
:data:`metaproc.docs.TOPIC_REGISTRY` so the skill file stays in sync with the
actual bundled docs without manual updates.
"""

from __future__ import annotations

from metaproc.docs import TOPIC_REGISTRY as _TOPIC_REGISTRY
from metaproc.skill.spec import SkillSpec


def _help_topic_catalog() -> str:
    """Generate a catalog section listing ``metaproc help`` topics.

    Sources the topics from :data:`metaproc.docs.TOPIC_REGISTRY` so the list never
    goes stale. Registry order is preserved rather than sorted: it is the
    recommended reading order, and alphabetical order would interleave the
    component architecture references with the documents an agent should read
    first. Each entry carries its approximate size, so an agent can judge what a
    topic costs before spending context on it.
    """

    lines: list[str] = []
    lines.append("## Help Topics")
    lines.append("")
    lines.append("Available via `metaproc help <topic>`, in recommended reading order:")
    lines.append("")

    for topic in _TOPIC_REGISTRY:
        size = f"~{topic.words / 1000:.1f}k words"
        lines.append(f"- **{topic.name}** ({size}): {topic.description}")

    return "\n".join(lines)


def metaproc_skill_spec() -> SkillSpec:
    """Factory function returning the metaproc SkillSpec.

    Registered as the ``metaproc`` entry in the ``metaproc.skills``
    entry-point group.
    """
    return SkillSpec(
        name="metaproc",
        description=(
            "Route Metaproc process launches, resumes, monitoring, and supervision "
            "through its CLI and bundled manuals. Use when running `metaproc "
            "run-process` or `metaproc run-step`, supervising a run, or deciding "
            "whether orchestration belongs in Metaproc. Read `metaproc help operator` "
            "before operating a run."
        ),
        # Space-separated per the Agent Skills specification. `uv run metaproc`
        # invocations are not pre-approved: entries with embedded spaces cannot be
        # expressed in the space-separated form, so source checkouts prompt instead.
        allowed_tools="Bash(metaproc:*) Read",
        baseline_package="metaproc.skill.baselines",
        baseline_name="metaproc",
        catalog_fn=_help_topic_catalog,
    )
