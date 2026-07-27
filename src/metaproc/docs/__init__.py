"""Bundled metaproc documentation, served at runtime by ``metaproc help <topic>``.

The ``.md`` files in this package ship inside the wheel, so they are readable via
``importlib.resources`` under both source and installed/zipped runs without a
``force-include`` entry. :class:`HelpTopics` maps each help topic to its bundled
doc via :func:`metaproc.resource_docs.resource_doc_field` (loaded lazily on
construction).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from metaproc.resource_docs import resource_doc_field

_PACKAGE = "metaproc.docs"

# One-line description per `metaproc help` topic — the single source of truth shared by
# `metaproc help` (commands/help.py) and the `metaproc skill` catalog (skill/builtin.py).
TOPIC_DESCRIPTIONS: dict[str, str] = {
    "operator": "Runtime CLI reference: running, monitoring, and resuming processes.",
    "developer": "Extending metaproc + the 'metaproc is the right wrapper' policy.",
    "concepts": "First-principles design concepts and the process model.",
}


@dataclass(frozen=True)
class HelpTopics:
    """One field per ``metaproc help`` topic, defaulting to its bundled markdown."""

    operator: str = resource_doc_field(_PACKAGE, "metaproc-operator-reference")
    developer: str = resource_doc_field(_PACKAGE, "metaproc-developer-guide")
    concepts: str = resource_doc_field(_PACKAGE, "metaproc-concepts-and-principles")


@cache
def load_help_topics() -> HelpTopics:
    """Return the process-wide :class:`HelpTopics` (docs read once, then cached)."""
    return HelpTopics()
