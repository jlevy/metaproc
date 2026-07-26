"""Trace extractors. Each extractor reads one source's on-disk logs and
emits ``TraceEvent`` spans without re-instrumenting the source.

Public surface:

- :class:`TraceExtractor` — protocol implemented by each extractor.
- :func:`all_extractors` — registered V1 extractors, iterated by
  ``metaproc trace --extract``. Includes the framework-bundled
  extractors (Claude per-attempt JSONL + metaproc engine/runpool
  events) plus any extractor registered under the
  ``metaproc.trace.extractors`` entry-point group by another installed
  package (workflow plugins typically register their own extractors
  this way).

Plan reference: § Extractor protocol + § V1 extractors.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from metaproc.trace.extractors.claude_agent import ClaudeAgentExtractor
from metaproc.trace.extractors.codex_agent import CodexAgentExtractor
from metaproc.trace.extractors.gemini_agent import GeminiAgentExtractor
from metaproc.trace.extractors.metaproc_engine import MetaprocEngineExtractor
from metaproc.trace.extractors.pi_agent import PiAgentExtractor
from metaproc.trace.schema import TraceEvent

log = logging.getLogger(__name__)


@runtime_checkable
class TraceExtractor(Protocol):
    """One extractor per source. Stateless; ``detect`` and ``extract`` are
    called per ``run_dir``.
    """

    source: str
    """Stable source name (matches ``TraceEvent.source``)."""

    def detect(self, run_dir: Path) -> bool:
        """Return ``True`` iff this extractor has data to read in ``run_dir``."""
        ...

    def extract(self, run_dir: Path, *, trace_id: str) -> Iterable[TraceEvent]:
        """Yield spans for ``run_dir``. ``trace_id`` is propagated by the
        CLI from the dispatch's ``RUN_ID``.
        """
        ...


_ENTRY_POINT_GROUP = "metaproc.trace.extractors"


def all_extractors() -> list[TraceExtractor]:
    """Return all registered V1 extractors. Order: framework-bundled
    first, then entry-point-registered (workflow-specific) extractors.

    The metaproc framework ships two domain-agnostic extractors:
    - ``MetaprocEngineExtractor`` — reads metaproc's own DAG + runpool
      events.
    - ``ClaudeAgentExtractor`` — reads per-attempt Claude JSONL (any
      metaproc workflow that runs the claude-code-cli adapter).

    Workflow-specific extractors (e.g. ``external-wrapper``, ``web-bundle``
    for the consumer workflow workflow) register via Python entry points and load
    here.
    """

    extractors: list[TraceExtractor] = [
        MetaprocEngineExtractor(),
        ClaudeAgentExtractor(),
        CodexAgentExtractor(),
        GeminiAgentExtractor(),
        PiAgentExtractor(),
    ]

    for ep in entry_points(group=_ENTRY_POINT_GROUP):
        try:
            cls = ep.load()
            extractors.append(cls())
        except Exception as exc:  # noqa: BLE001 — log + continue, don't crash trace
            log.warning("Failed to load trace extractor entry-point %r: %s", ep.name, exc)

    return extractors
