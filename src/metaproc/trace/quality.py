"""Generic trace quality validators.

Both validators run over the loaded trace and surface per-(step, item)
verdicts so the operator can spot agent sessions that didn't follow the
runbook.

- ``runbook-completion``: did each ``agent_session`` produce a ``Write``
  tool_call? The runbook always demands writing the declared output
  file, so a session with zero ``Write`` calls is a near-certain
  failure mode (the agent stopped at a text response).

- ``directive-compliance``: did the agent read the inputs declared by the
  active workflow plugin for its step?
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from metaproc.trace.schema import TraceEvent

VALIDATOR_NAMES: frozenset[str] = frozenset({"runbook-completion", "directive-compliance"})


def runbook_completion_rows(spans: list[TraceEvent]) -> list[dict[str, Any]]:
    """Return one row per ``agent_session`` describing whether the agent
    wrote anything (``status: ok``) or didn't (``status: missing_output``).

    Aggregates ``Write`` tool_call counts under each session via the
    parent_span_id graph.

    Sessions whose ``ts_end`` is null are classified as ``in_progress``
    rather than ``missing_output`` — they're still in-flight and have
    not yet had a chance to write. See this regression: noisy
    "missing_output" rows during active runs were the original
    motivator for active-run awareness in the trace health command.
    """
    sessions = [s for s in spans if s.kind == "agent_session"]
    if not sessions:
        return []
    writes_by_session: dict[str, int] = defaultdict(int)
    for s in spans:
        if s.kind != "tool_call":
            continue
        if s.attributes.get("tool.name") not in ("Write", "Edit"):
            continue
        parent = s.parent_span_id
        if parent:
            writes_by_session[parent] += 1
    rows: list[dict[str, Any]] = []
    for session in sessions:
        write_count = writes_by_session.get(session.span_id, 0)
        if session.ts_end is None:
            status = "in_progress"
        elif write_count > 0:
            status = "ok"
        else:
            status = "missing_output"
        rows.append(
            {
                "step.id": session.attributes.get("step.id"),
                "item.key": session.attributes.get("item.key"),
                "agent.session_id": session.attributes.get("agent.session_id"),
                "writes": write_count,
                "status": status,
            }
        )
    rows.sort(key=lambda r: (str(r["step.id"]), str(r["item.key"])))
    return rows


def directive_compliance_rows(
    spans: list[TraceEvent],
    directives_by_step: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Return per-(agent_session) coverage of expected input reads.

    Each row carries ``step.id``, ``item.key``, ``expected`` (count of
    directives), ``read`` (count satisfied), and a ``missing`` list of
    unread directive substrings.
    """
    directives_by_step = directives_by_step or {}
    sessions = [s for s in spans if s.kind == "agent_session"]
    if not sessions:
        return []
    reads_by_session: dict[str, list[str]] = defaultdict(list)
    for s in spans:
        if s.kind != "tool_call":
            continue
        if s.attributes.get("tool.name") != "Read":
            continue
        parent = s.parent_span_id
        if not parent:
            continue
        file_path = s.attributes.get("tool.input.file_path")
        if isinstance(file_path, str):
            reads_by_session[parent].append(file_path)
    rows: list[dict[str, Any]] = []
    for session in sessions:
        step_id = session.attributes.get("step.id")
        directives = directives_by_step.get(str(step_id), ())
        if not directives:
            continue
        reads = reads_by_session.get(session.span_id, [])
        missing: list[str] = []
        for directive in directives:
            if not any(directive in r for r in reads):
                missing.append(directive)
        # In-flight session: the agent may still read more inputs.
        # Same active-run awareness applied to runbook_completion above
        # (this regression).
        if session.ts_end is None:
            status = "in_progress"
        elif missing:
            status = "non_compliant"
        else:
            status = "ok"
        rows.append(
            {
                "step.id": step_id,
                "item.key": session.attributes.get("item.key"),
                "expected": len(directives),
                "read": len(directives) - len(missing),
                "missing": missing,
                "status": status,
            }
        )
    rows.sort(key=lambda r: (str(r["step.id"]), str(r["item.key"])))
    return rows


def format_runbook_completion(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no agent_session spans in trace)\n"
    lines = ["step.id                       item.key  writes  status"]
    for row in rows:
        lines.append(
            f"{str(row['step.id'] or ''):<30}{str(row['item.key'] or ''):<12} "
            f"{row['writes']:<6}  {row['status']}"
        )
    total = len(rows)
    missing = sum(1 for r in rows if r["status"] == "missing_output")
    in_progress = sum(1 for r in rows if r["status"] == "in_progress")
    footer = f"\n{total} agent_session(s); {missing} missing declared output"
    if in_progress:
        footer += f"; {in_progress} in-progress"
    lines.append(footer)
    return "\n".join(lines) + "\n"


def format_directive_compliance(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no eligible agent_session spans in trace)\n"
    lines = ["step.id                       item.key  read/expected  status        missing"]
    for row in rows:
        missing = ", ".join(row["missing"]) if row["missing"] else "-"
        lines.append(
            f"{str(row['step.id'] or ''):<30}{str(row['item.key'] or ''):<12} "
            f"{row['read']}/{row['expected']:<11}  {row['status']:<13} {missing}"
        )
    total = len(rows)
    non = sum(1 for r in rows if r["status"] == "non_compliant")
    in_progress = sum(1 for r in rows if r["status"] == "in_progress")
    footer = f"\n{total} agent_session(s); {non} non_compliant"
    if in_progress:
        footer += f"; {in_progress} in-progress"
    lines.append(footer)
    return "\n".join(lines) + "\n"
