"""Bundled metaproc documentation, served at runtime by ``metaproc help <topic>``.

The ``.md`` files in this package ship inside the wheel, so they are readable via
``importlib.resources`` under both source and installed/zipped runs without a
``force-include`` entry. :data:`TOPIC_REGISTRY` is the single source of truth for
which topics exist, which file backs each one, and roughly how large it is;
:class:`HelpTopics` loads the three original topics lazily via
:func:`metaproc.resource_docs.resource_doc_field`.

Two rules govern this package:

1. Everything here ships. A document that is project-internal — a future-work
   backlog, an authoring revision history, instructions for maintaining this
   repository — belongs under ``docs/project/`` instead.
2. A relative link in one of these documents must resolve inside ``src/metaproc/``.
   Anything else is dead for a reader of the installed wheel even though
   ``devtools/check_links.py`` resolves it happily against a checkout.
   ``devtools/check_shipped_links.py`` enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from metaproc.resource_docs import load_resource_md, resource_doc_field

_PACKAGE = "metaproc.docs"


@dataclass(frozen=True)
class Topic:
    """One ``metaproc help`` topic: its name, backing document, and rough size.

    ``words`` is approximate and exists only to tell a reader what a topic costs
    before they ask for it — the largest is roughly 30k tokens. It is maintained by
    hand and checked by ``tests/commands/test_help_command.py`` against a generous
    tolerance, because an exact count would churn on every prose edit.
    """

    name: str
    doc: str
    description: str
    words: int

    @property
    def filename(self) -> str:
        """Return the bundled markdown filename backing this topic."""
        return f"{self.doc}.md"


# Ordered deliberately: the three-document reading path first, then the reference
# documents, then the operator runbooks, then the component architecture references.
# `metaproc help` prints them in this order, so the listing doubles as the
# recommended reading order.
TOPIC_REGISTRY: tuple[Topic, ...] = (
    Topic(
        "concepts",
        "metaproc-concepts-and-principles",
        "Start here. Vocabulary, ownership boundaries, step modes, optimization loops.",
        7000,
    ),
    Topic(
        "design",
        "metaproc-design",
        "How Metaproc is built: spec format, runtime artifacts, adapters, robustness.",
        18400,
    ),
    Topic(
        "framework",
        "process-framework-concepts",
        "The general execution model beneath any process framework, and Metaproc's mapping.",
        7200,
    ),
    Topic(
        "operator",
        "metaproc-operator-reference",
        "Runtime CLI reference: running, monitoring, and resuming processes.",
        5400,
    ),
    Topic(
        "developer",
        "metaproc-developer-guide",
        "Extending Metaproc and the 'Metaproc is the right wrapper' policy.",
        1300,
    ),
    Topic(
        "conventions",
        "conventions",
        "Framework-level naming, structure, and file-format rules.",
        4500,
    ),
    Topic(
        "artifacts",
        "artifact-catalog",
        "Every runtime artifact Metaproc writes or reads: format, schema, lifecycle.",
        1400,
    ),
    Topic(
        "execution-model",
        "execution-model-design",
        "Durable contracts under task-level scheduling, and what is deliberately left out.",
        1900,
    ),
    Topic(
        "credentials",
        "credential-setup.runbook",
        "Configuring credentials for each adapter: Claude, Codex, Gemini, pi, GCP.",
        2400,
    ),
    Topic(
        "cloud-dispatch",
        "cloud-dispatch.runbook",
        "Preparing, submitting, monitoring, and recovering GCP Batch workloads.",
        1500,
    ),
    Topic(
        "arch-auth",
        "arch-authentication",
        "Architecture: credential pools, adapter auth modes, and secret handling.",
        8700,
    ),
    Topic(
        "arch-cloud",
        "arch-cloud-execution",
        "Architecture: GCP Batch dispatch, orchestrator and worker placement.",
        5300,
    ),
    Topic(
        "arch-runpool",
        "arch-runpool",
        "Architecture: local process manager, adaptive concurrency, memory pressure.",
        3800,
    ),
    Topic(
        "arch-harness",
        "arch-claude-code-harness",
        "Architecture: the Claude Code adapter harness and its wire format.",
        2900,
    ),
    Topic(
        "arch-execution",
        "arch-execution-model",
        "Architecture: how the execution model is implemented today, including resume.",
        2400,
    ),
    Topic(
        "arch-testing",
        "arch-testing",
        "Architecture: the test tiers, when to use each, and per-adapter credentials.",
        1100,
    ),
    Topic(
        "arch-file-io",
        "arch-file-io-utilities",
        "Architecture: the curated metaproc.io surface and frontmatter gotchas.",
        1000,
    ),
)

TOPIC_BY_NAME: dict[str, Topic] = {topic.name: topic for topic in TOPIC_REGISTRY}

# Topic descriptions are shared by `metaproc help` (commands/help.py) and the
# `metaproc skill` catalog (skill/builtin.py) so the two interfaces cannot drift.
# Derived from TOPIC_REGISTRY; kept as a plain dict[str, str] because it is part
# of this module's established surface.
TOPIC_DESCRIPTIONS: dict[str, str] = {topic.name: topic.description for topic in TOPIC_REGISTRY}


@dataclass(frozen=True)
class HelpTopics:
    """The three original ``metaproc help`` topics, each lazily loaded.

    Retained for its established shape. Topics added by the documentation
    reorganization are served through :func:`topic_markdown`, which reads any
    registry entry on demand: at seventeen topics a field-per-topic dataclass would
    read every document to serve one, and cannot express a hyphenated topic name at
    all.
    """

    operator: str = resource_doc_field(_PACKAGE, "metaproc-operator-reference")
    developer: str = resource_doc_field(_PACKAGE, "metaproc-developer-guide")
    concepts: str = resource_doc_field(_PACKAGE, "metaproc-concepts-and-principles")


@cache
def load_help_topics() -> HelpTopics:
    """Return the process-wide :class:`HelpTopics` (docs read once, then cached)."""
    return HelpTopics()


@cache
def topic_markdown(name: str) -> str:
    """Return the markdown body for ``name``, reading only that topic's document.

    Raises :class:`KeyError` for an unknown topic, so a caller can tell a bad topic
    name from a document that fails to load.
    """
    topic = TOPIC_BY_NAME[name]
    return load_resource_md(_PACKAGE, topic.doc)
