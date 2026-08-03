"""Unified log parsing for Claude Code, Gemini CLI, and Pi CLI JSONL output.

Provides:
  LogEvent       — normalized event dataclass
  LogParser      — per-adapter parser protocol
  ClaudeLogParser — parser for Claude Code stream-json
  GeminiLogParser — parser for Gemini CLI stream-json (with delta coalescing)
  PiLogParser    — parser for Pi CLI JSON mode JSONL
  detect_adapter — auto-detect adapter from first JSON line
  render_log_event — compact one-liner renderer
  LogFile        — file watcher with parser integration
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from prettyfmt import fmt_timedelta

from metaproc.io import ArtifactPath, logical_path
from metaproc.logutil.claude_usage import claude_attempt_usage
from metaproc.logutil.usage import (
    UsageStats,
    extract_gemini_usage,
    sum_codex_usage,
    sum_pi_usage,
)

log = logging.getLogger(__name__)

# ── Normalized event ─────────────────────────────────────────────


@dataclass
class LogEvent:
    """A normalized log event from any adapter."""

    kind: str  # "init", "tool_call", "tool_result", "text", "thinking", "rate_limit", "result", "system", "raw"
    summary: str  # compact one-liner for display
    adapter: str = "unknown"  # "claude", "gemini", "pi", "unknown"
    provider: str | None = (
        None  # "anthropic", "vertex-maas", "openai", etc. — set on rate_limit events
    )
    timestamp: str | None = None
    is_error: bool = False
    is_done: bool = False  # True for result events (DONE or FAIL)
    cost_usd: float | None = None
    duration_s: float | None = None
    raw: dict[str, Any] | str = field(default_factory=dict)
    summary_parts: list[dict[str, Any]] | None = (
        None  # structured [{type, text}] for rich rendering
    )


# ── Parser protocol ──────────────────────────────────────────────


@runtime_checkable
class LogParser(Protocol):
    adapter_name: str

    def parse_line(self, line: str) -> list[LogEvent]:
        """Parse a raw line, return zero or more LogEvents."""
        ...

    def flush(self) -> list[LogEvent]:
        """Flush any buffered state (e.g. pending Gemini deltas)."""
        ...


# ── Helpers ──────────────────────────────────────────────────────


def _parse_json(line: str) -> dict[str, Any] | None:
    """Try to parse a line as JSON. Returns None on failure."""
    try:
        obj: Any = json.loads(line)
        if isinstance(obj, dict):
            return cast(dict[str, Any], obj)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _format_tool_params(inp: dict[str, Any]) -> str:
    """Extract key tool input params for compact display."""
    key_params = [
        f"{k}={str(inp[k])}"
        for k in ("command", "file_path", "pattern", "prompt", "path", "query", "content")
        if k in inp
    ]
    return " ".join(key_params[:2]) if key_params else ""


def _tool_call_summary_parts(tool_name: str, inp: dict[str, Any]) -> list[dict[str, Any]]:
    """Build structured summary parts for a tool call event."""
    parts: list[dict[str, Any]] = [{"type": "label", "text": f"[call:{tool_name}]"}]
    _KEY_NAMES = ("command", "file_path", "pattern", "prompt", "path", "query", "content")
    count = 0
    for k in _KEY_NAMES:
        if k in inp and count < 2:
            parts.append({"type": "key", "text": f" {k}="})
            parts.append({"type": "value", "text": str(inp[k])})
            count += 1
    return parts


def _tool_result_preview(result_content: str | list[Any] | object) -> str:
    """Extract a text preview from tool result content."""
    if isinstance(result_content, str):
        return result_content.replace("\n", " ").strip()
    if isinstance(result_content, list):
        typed_list: list[Any] = cast(list[Any], result_content)
        texts: list[str] = []
        for item in typed_list:
            if isinstance(item, dict):
                b: dict[str, Any] = cast(dict[str, Any], item)
                texts.append(b.get("text", str(b)))
            else:
                texts.append(str(item))
        return " ".join(texts).replace("\n", " ").strip()
    return str(result_content).replace("\n", " ").strip()


def _format_duration(duration_ms: float | None, duration_s: float | None) -> str:
    if duration_ms is not None:
        return f" duration={fmt_timedelta(duration_ms / 1000)}"
    if duration_s is not None:
        return f" duration={fmt_timedelta(duration_s)}"
    return ""


def _format_cost(cost_usd: float | None) -> str:
    if cost_usd is not None:
        return f" cost=${cost_usd:.2f}"
    return ""


# ── Claude Code parser ───────────────────────────────────────────


class ClaudeLogParser:
    """Parser for Claude Code stream-json JSONL output."""

    adapter_name: str = "claude"

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            return [
                LogEvent(
                    kind="raw",
                    summary=f"[raw] {line.strip()}",
                    adapter=self.adapter_name,
                    raw=line,
                )
            ]

        etype = event.get("type", "unknown")
        ts = event.get("timestamp") or event.get("uuid")

        # System events
        if etype == "system":
            subtype = event.get("subtype", "")
            if subtype == "init":
                session_id = event.get("session_id", "?")
                model = event.get("model", "?")
                tools: Any = event.get("tools", [])
                tool_count: int = len(cast(list[Any], tools)) if isinstance(tools, list) else 0
                return [
                    LogEvent(
                        kind="init",
                        adapter=self.adapter_name,
                        timestamp=ts,
                        summary=f"[init] session={session_id[:8]} model={model} tools={tool_count}",
                        raw=event,
                    )
                ]
            # Skip noisy hook events
            if subtype in ("hook_started", "hook_response"):
                return []
            return [
                LogEvent(
                    kind="system",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[system:{subtype}] {str(event)}",
                    raw=event,
                )
            ]

        # Rate limit events — emit structured event. Claude Code only talks to
        # Anthropic directly, so provider is pinned to "anthropic".
        if etype == "rate_limit_event":
            info = event.get("rate_limit_info", {})
            info_dict: dict[str, Any] = cast(dict[str, Any], info) if isinstance(info, dict) else {}
            status = str(info_dict.get("status", "unknown"))
            limit_type = str(info_dict.get("rateLimitType", "unknown"))
            return [
                LogEvent(
                    kind="rate_limit",
                    adapter=self.adapter_name,
                    provider="anthropic",
                    timestamp=ts,
                    is_error=status == "blocked",
                    summary=f"[rate_limit:{status}] type={limit_type}",
                    raw=event,
                )
            ]

        # Assistant messages (contain tool_use and text blocks)
        if etype == "assistant":
            return self._parse_assistant(event, ts)

        # User messages (contain tool_result blocks)
        if etype == "user":
            return self._parse_user(event, ts)

        # Result (session completion)
        if etype == "result":
            return self._parse_result(event, ts)

        return [
            LogEvent(
                kind=etype,
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[{etype}] {str(event)}",
                raw=event,
            )
        ]

    def flush(self) -> list[LogEvent]:
        return []

    def _parse_assistant(self, event: dict[str, Any], ts: str | None) -> list[LogEvent]:
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            return [
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[text] {str(content)}",
                    raw=event,
                )
            ]

        blocks: list[Any] = cast(list[Any], content)
        events: list[LogEvent] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            b: dict[str, Any] = cast(dict[str, Any], block)
            btype: str | None = b.get("type")
            if btype == "tool_use":
                tool_name: str = b.get("name", "?")
                inp: Any = b.get("input", {})
                inp_dict: dict[str, Any] = (
                    cast(dict[str, Any], inp) if isinstance(inp, dict) else {}
                )
                param_str = _format_tool_params(inp_dict) if isinstance(inp, dict) else str(inp)
                parts = (
                    _tool_call_summary_parts(tool_name, inp_dict) if isinstance(inp, dict) else None
                )
                events.append(
                    LogEvent(
                        kind="tool_call",
                        adapter=self.adapter_name,
                        timestamp=ts,
                        summary=f"[call:{tool_name}] {param_str}",
                        summary_parts=parts,
                        raw=event,
                    )
                )
            elif btype == "text":
                text_val: str = b.get("text", "")
                if text_val.strip():
                    events.append(
                        LogEvent(
                            kind="text",
                            adapter=self.adapter_name,
                            timestamp=ts,
                            summary=f"[text] {text_val}",
                            raw=event,
                        )
                    )
            elif btype == "thinking":
                thinking_text: str = b.get("thinking", "")
                if thinking_text.strip():
                    events.append(
                        LogEvent(
                            kind="thinking",
                            adapter=self.adapter_name,
                            timestamp=ts,
                            summary=f"[thinking] {thinking_text}",
                            raw=event,
                        )
                    )

        if not events:
            # If all blocks were thinking-only, suppress entirely
            content_dicts: list[dict[str, Any]] = [
                cast(dict[str, Any], cb) for cb in blocks if isinstance(cb, dict)
            ]
            if all(cd.get("type") == "thinking" for cd in content_dicts):
                return []
            return [
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[assistant] {str(blocks)}",
                    raw=event,
                )
            ]
        return events

    def _parse_user(self, event: dict[str, Any], ts: str | None) -> list[LogEvent]:
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            return [
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[user] {str(content)}",
                    raw=event,
                )
            ]

        user_blocks: list[Any] = cast(list[Any], content)
        events: list[LogEvent] = []
        for block in user_blocks:
            if not isinstance(block, dict):
                continue
            blk: dict[str, Any] = cast(dict[str, Any], block)
            if blk.get("type") == "tool_result":
                is_error: bool = blk.get("is_error", False)
                result_content: Any = blk.get("content", "")
                if isinstance(result_content, str):
                    size = len(result_content)
                elif isinstance(result_content, list):
                    rc_list: list[Any] = cast(list[Any], result_content)
                    size = sum(len(str(rc)) for rc in rc_list)
                else:
                    size = len(str(result_content))
                preview = _tool_result_preview(cast("str | list[Any] | object", result_content))
                error_str = " ERROR" if is_error else ""
                events.append(
                    LogEvent(
                        kind="tool_result",
                        adapter=self.adapter_name,
                        timestamp=ts,
                        summary=f"[result]{error_str} {preview}",
                        summary_parts=[
                            {"type": "label", "text": f"[result]{error_str}"},
                            {"type": "value", "text": f" {preview}"},
                            {"type": "omitted", "text": f" ({size} chars)"},
                        ],
                        is_error=is_error,
                        raw=event,
                    )
                )

        if not events:
            return [
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[user] {str(user_blocks)}",
                    raw=event,
                )
            ]
        return events

    def _parse_result(self, event: dict[str, Any], ts: str | None) -> list[LogEvent]:
        is_error = event.get("is_error", False)
        cost_usd = event.get("cost_usd") or event.get("total_cost_usd")
        duration_ms = event.get("duration_ms")
        duration_s = event.get("duration_s") or event.get("session_duration_seconds")

        cost_str = _format_cost(cost_usd)
        dur_str = _format_duration(duration_ms, duration_s)

        dur_s = None
        if duration_ms is not None:
            dur_s = duration_ms / 1000
        elif duration_s is not None:
            dur_s = float(duration_s)

        if is_error:
            error_msg = event.get("error", "")
            return [
                LogEvent(
                    kind="result",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[FAIL]{cost_str}{dur_str} {str(error_msg)}",
                    is_error=True,
                    is_done=True,
                    cost_usd=cost_usd,
                    duration_s=dur_s,
                    raw=event,
                )
            ]
        return [
            LogEvent(
                kind="result",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[DONE]{cost_str}{dur_str}",
                is_done=True,
                cost_usd=cost_usd,
                duration_s=dur_s,
                raw=event,
            )
        ]


# ── Gemini CLI parser ────────────────────────────────────────────


class GeminiLogParser:
    """Parser for Gemini CLI stream-json JSONL output with delta coalescing."""

    adapter_name: str = "gemini"

    def __init__(self) -> None:
        self._delta_buf: list[str] = []
        self._delta_ts: str | None = None

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            # Non-JSON noise — skip common Gemini stderr noise silently
            stripped = line.strip()
            if self._is_noise(stripped):
                return []
            return self._flush_then(
                LogEvent(
                    kind="raw",
                    summary=f"[raw] {stripped}",
                    adapter=self.adapter_name,
                    raw=line,
                )
            )

        etype = event.get("type", "unknown")
        ts = event.get("timestamp")

        # Init event
        if etype == "init":
            session_id = event.get("session_id", "?")
            model = event.get("model", "?")
            return self._flush_then(
                LogEvent(
                    kind="init",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[init] session={session_id[:8]} model={model}",
                    raw=event,
                )
            )

        # Message events — may be delta chunks
        if etype == "message":
            return self._parse_message(event, ts)

        # Tool use (top-level in Gemini)
        if etype == "tool_use":
            tool_name: str = event.get("tool_name", "?")
            params: Any = event.get("parameters", {})
            params_dict: dict[str, Any] = (
                cast(dict[str, Any], params) if isinstance(params, dict) else {}
            )
            param_str = (
                _format_tool_params(params_dict) if isinstance(params, dict) else str(params)
            )
            parts = (
                _tool_call_summary_parts(tool_name, params_dict)
                if isinstance(params, dict)
                else None
            )
            return self._flush_then(
                LogEvent(
                    kind="tool_call",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[call:{tool_name}] {param_str}",
                    summary_parts=parts,
                    raw=event,
                )
            )

        # Tool result (top-level in Gemini)
        if etype == "tool_result":
            tool_id = event.get("tool_id", "?")
            status = event.get("status", "?")
            output = event.get("output", "")
            size = len(output) if isinstance(output, str) else len(str(output))
            is_error = status != "success"
            error_str = f" {status.upper()}" if is_error else ""
            preview = _tool_result_preview(output)
            return self._flush_then(
                LogEvent(
                    kind="tool_result",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[result:{tool_id}]{error_str} {preview}",
                    summary_parts=[
                        {"type": "label", "text": f"[result:{tool_id}]{error_str}"},
                        {"type": "value", "text": f" {preview}"},
                        {"type": "omitted", "text": f" ({size} chars)"},
                    ],
                    is_error=is_error,
                    raw=event,
                )
            )

        # Result (session completion)
        if etype == "result":
            return self._flush_then(*self._parse_result(event, ts))

        return self._flush_then(
            LogEvent(
                kind=etype,
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[{etype}] {str(event)}",
                raw=event,
            )
        )

    def flush(self) -> list[LogEvent]:
        """Flush any buffered delta chunks."""
        if not self._delta_buf:
            return []
        text = "".join(self._delta_buf)
        self._delta_buf.clear()
        ts = self._delta_ts
        self._delta_ts = None
        if not text.strip():
            return []
        return [
            LogEvent(
                kind="text",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[text] {text}",
                raw={"_coalesced": True, "text": text},
            )
        ]

    def _flush_then(self, *events: LogEvent) -> list[LogEvent]:
        """Flush pending deltas then append the given events."""
        result = self.flush()
        result.extend(events)
        return result

    def _parse_message(self, event: dict[str, Any], ts: str | None) -> list[LogEvent]:
        role = event.get("role", "")
        content = event.get("content", "")
        is_delta = event.get("delta", False)

        # User messages (prompt) — show as-is
        if role == "user":
            return self._flush_then(
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[user] {str(content)}",
                    raw=event,
                )
            )

        # Assistant delta chunks — accumulate
        if is_delta and role == "assistant":
            if not self._delta_buf:
                self._delta_ts = ts
            self._delta_buf.append(content if isinstance(content, str) else str(content))
            return []

        # Non-delta assistant message
        return self._flush_then(
            LogEvent(
                kind="text",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[text] {str(content)}",
                raw=event,
            )
        )

    def _parse_result(self, event: dict[str, Any], ts: str | None) -> list[LogEvent]:
        status = event.get("status", "")
        is_error = status != "success"
        stats = event.get("stats", {})
        duration_ms = stats.get("duration_ms")
        cost_usd = None

        dur_str = _format_duration(duration_ms, None)
        cost_str = _format_cost(cost_usd)

        dur_s = duration_ms / 1000 if duration_ms is not None else None

        # Token summary
        total_tokens = stats.get("total_tokens")
        tool_calls = stats.get("tool_calls")
        extra = ""
        if total_tokens is not None:
            extra += f" tokens={total_tokens:,}"
        if tool_calls is not None:
            extra += f" tools={tool_calls}"

        if is_error:
            error_msg = event.get("error", status)
            return [
                LogEvent(
                    kind="result",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[FAIL]{cost_str}{dur_str}{extra} {str(error_msg)}",
                    is_error=True,
                    is_done=True,
                    cost_usd=cost_usd,
                    duration_s=dur_s,
                    raw=event,
                )
            ]
        return [
            LogEvent(
                kind="result",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[DONE]{cost_str}{dur_str}{extra}",
                is_done=True,
                cost_usd=cost_usd,
                duration_s=dur_s,
                raw=event,
            )
        ]

    @staticmethod
    def _is_noise(line: str) -> bool:
        """Return True for Gemini stderr noise that should be suppressed."""
        error_signals = (
            "Attempt ",
            "failed with status",
            "Error:",
            "ApiError:",
            "FATAL",
            "WARN",
            "error:",
            "fatal:",
        )
        return not any(sig in line for sig in error_signals)


def _extract_pi_thinking_events(
    event: dict[str, Any], timestamp: str | None = None
) -> list[LogEvent]:
    """Extract thinking and text blocks from a Pi ``message_final`` or ``message_end``.

    For ``message_final``: content is in ``assistantMessageEvent.partial.content``.
    For ``message_end``: content is in ``message.content``.
    Returns ``LogEvent(kind="thinking")`` for thinking blocks and
    ``LogEvent(kind="text")`` for text blocks.
    """
    # Try message_final structure first, then message_end structure.
    partial = event.get("assistantMessageEvent", {}).get("partial") or event.get("partial")
    content = partial.get("content", []) if partial else event.get("message", {}).get("content", [])

    events: list[LogEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        b: dict[str, Any] = cast(dict[str, Any], block)
        btype: str = b.get("type", "")
        if btype == "thinking":
            thinking_text: str = b.get("thinking", "")
            if thinking_text.strip():
                events.append(
                    LogEvent(
                        kind="thinking",
                        summary=f"[thinking] {thinking_text}",
                        adapter="pi",
                        timestamp=timestamp,
                        raw=event,
                    )
                )
        elif btype == "text":
            text: str = b.get("text", "")
            if text.strip():
                events.append(
                    LogEvent(
                        kind="text",
                        summary=f"[pi] {text}",
                        adapter="pi",
                        timestamp=timestamp,
                        raw=event,
                    )
                )
    return events


# ── Pi CLI parser ────────────────────────────────────────────────


class PiLogParser:
    """Parser for Pi CLI JSON mode (``--mode json``) JSONL output.

    Pi emits events: ``agent_start``, ``turn_start``, ``message_start``,
    ``message_update``, ``message_end``, ``tool_execution_start``,
    ``tool_execution_update``, ``tool_execution_end``, ``turn_end``,
    ``agent_end``.
    """

    adapter_name: str = "pi"

    @staticmethod
    def _extract_timestamp(event: dict[str, Any]) -> str | None:
        """Extract an ISO timestamp from a Pi event.

        Pi uses ISO strings on ``session`` events and epoch-ms integers
        inside the nested ``message`` object on other events.
        """
        # Top-level ISO string (session events)
        ts = event.get("timestamp")
        if isinstance(ts, str):
            return ts
        # Nested message.timestamp (epoch ms)
        msg = event.get("message")
        if isinstance(msg, dict):
            msg_ts = msg.get("timestamp")
            if isinstance(msg_ts, (int, float)):
                return datetime.fromtimestamp(msg_ts / 1000, tz=UTC).isoformat()
        return None

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            if line.strip():
                return [LogEvent(kind="raw", summary=f"[raw] {line.strip()}", adapter="pi")]
            return []

        etype = event.get("type", "")
        ts = self._extract_timestamp(event)

        if etype == "session":
            session_id = event.get("id", "?")
            return [
                LogEvent(
                    kind="init",
                    summary=f"[init] session={session_id[:8]}",
                    adapter="pi",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "agent_start":
            model = event.get("model", "unknown")
            return [
                LogEvent(
                    kind="init",
                    summary=f"[init] model={model}",
                    adapter="pi",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "tool_execution_start":
            tool: str = event.get("toolName", event.get("tool", event.get("name", "?")))
            args: Any = event.get("args", {})
            args_dict: dict[str, Any] = cast(dict[str, Any], args) if isinstance(args, dict) else {}
            param_str = _format_tool_params(args_dict) if isinstance(args, dict) else str(args)
            parts = _tool_call_summary_parts(tool, args_dict) if isinstance(args, dict) else None
            return [
                LogEvent(
                    kind="tool_call",
                    summary=f"[call:{tool}] {param_str}",
                    summary_parts=parts,
                    adapter="pi",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "tool_execution_end":
            tool = event.get("toolName", event.get("tool", event.get("name", "?")))
            is_error = event.get("isError", False)
            result_data: Any = event.get("result", {})
            result_content: str = (
                cast(dict[str, Any], result_data).get("content", "")
                if isinstance(result_data, dict)
                else ""
            )
            preview = _tool_result_preview(result_content)
            size = len(preview)
            error_str = " ERROR" if is_error else ""
            return [
                LogEvent(
                    kind="tool_result",
                    summary=f"[result:{tool}]{error_str} {preview}",
                    summary_parts=[
                        {"type": "label", "text": f"[result:{tool}]{error_str}"},
                        {"type": "value", "text": f" {preview}"},
                        {"type": "omitted", "text": f" ({size} chars)"},
                    ],
                    is_error=is_error,
                    adapter="pi",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "message_update":
            content = str(event.get("content", ""))
            if content:
                return [
                    LogEvent(
                        kind="text",
                        summary=f"[pi] {content}",
                        adapter="pi",
                        timestamp=ts,
                        raw=event,
                    )
                ]
            return []

        if etype == "message_final":
            return _extract_pi_thinking_events(event, ts)

        if etype == "agent_end":
            return [
                LogEvent(
                    kind="result",
                    summary="[pi] agent finished",
                    adapter="pi",
                    timestamp=ts,
                    is_done=True,
                    raw=event,
                )
            ]

        if etype == "message_end":
            return _extract_pi_thinking_events(event, ts)

        if etype in ("turn_start", "turn_end", "message_start", "tool_execution_update"):
            return []

        return [
            LogEvent(
                kind="system",
                summary=f"[pi] {etype or 'unknown'}: {line.strip()[:80]}",
                adapter="pi",
                raw=event,
            )
        ]

    def flush(self) -> list[LogEvent]:
        return []


# ── RunPool log parser ──────────────────────────────────────────


class RunPoolLogParser:
    """Parser for runpool events JSONL files.

    Translates pool lifecycle events into ``LogEvent`` instances with
    descriptive summaries so the Log tab shows meaningful entries instead
    of ``[unknown]`` fallbacks.
    """

    adapter_name: str = "runpool"

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            if line.strip():
                return [LogEvent(kind="raw", summary=f"[raw] {line.strip()}", adapter="runpool")]
            return []

        etype = event.get("event", "")
        ts = event.get("ts")

        if etype == "pool_start":
            pool_id = event.get("pool_id", "?")
            backend = event.get("backend", "?")
            cap = event.get("max_concurrency", "?")
            return [
                LogEvent(
                    kind="init",
                    summary=f"[pool_start] {pool_id} backend={backend} max_concurrency={cap}",
                    adapter="runpool",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "pool_shutdown":
            completed = event.get("completed", 0)
            failed = event.get("failed", 0)
            killed = event.get("killed", 0)
            return [
                LogEvent(
                    kind="result",
                    summary=f"[pool_shutdown] completed={completed} failed={failed} killed={killed}",
                    adapter="runpool",
                    timestamp=ts,
                    is_done=True,
                    is_error=failed > 0 or killed > 0,
                    raw=event,
                )
            ]

        if etype == "process_start":
            label = event.get("label", "")
            pid = event.get("pid", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[process_start] pid={pid} {label}",
                    adapter="runpool",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "process_exit":
            label = event.get("label", "")
            pid = event.get("pid", "?")
            code = event.get("exit_code", "?")
            elapsed = event.get("elapsed_s")
            elapsed_str = f" {elapsed:.0f}s" if elapsed is not None else ""
            return [
                LogEvent(
                    kind="system",
                    summary=f"[process_exit] pid={pid} exit={code}{elapsed_str} {label}",
                    adapter="runpool",
                    timestamp=ts,
                    is_error=code != 0,
                    raw=event,
                )
            ]

        if etype == "process_kill":
            label = event.get("label", "")
            pid = event.get("pid", "?")
            reason = event.get("kill_reason", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[process_kill] pid={pid} reason={reason} {label}",
                    adapter="runpool",
                    timestamp=ts,
                    is_error=True,
                    raw=event,
                )
            ]

        if etype == "pressure_check":
            level = event.get("level", "?")
            pct = event.get("available_pct", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[pressure] {level} available={pct}%",
                    adapter="runpool",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "concurrency_adjust":
            old = event.get("old", "?")
            new = event.get("new", "?")
            reason = event.get("reason", "")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[concurrency] {old}\u2192{new} {reason}",
                    adapter="runpool",
                    timestamp=ts,
                    raw=event,
                )
            ]

        # Unknown runpool event
        return [
            LogEvent(
                kind="system",
                summary=f"[{etype or 'unknown'}] {line.strip()[:80]}",
                adapter="runpool",
                timestamp=ts,
                raw=event,
            )
        ]

    def flush(self) -> list[LogEvent]:
        return []


class ProcessLogParser:
    """Parser for process-events.jsonl files.

    Translates DAG orchestrator events (process start/complete, level
    start/complete, step start/complete/fail/skip/blocked, worker dispatch)
    into ``LogEvent`` instances.
    """

    adapter_name: str = "process"

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            return []

        ts = event.get("ts")
        etype = event.get("event", "")

        if etype == "process_start":
            process = event.get("process", "?")
            run_id = event.get("run_id", "?")
            backend = event.get("backend", "?")
            steps = event.get("step_count", "?")
            return [
                LogEvent(
                    kind="init",
                    summary=f"[process_start] {process} run={run_id} backend={backend} steps={steps}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "process_complete":
            process = event.get("process", "?")
            completed = event.get("completed", 0)
            failed = event.get("failed", 0)
            skipped = event.get("skipped", 0)
            elapsed = event.get("elapsed_s", 0)
            return [
                LogEvent(
                    kind="result",
                    summary=f"[process_complete] {process} completed={completed} failed={failed} skipped={skipped} {elapsed:.0f}s",
                    adapter="process",
                    timestamp=ts,
                    is_done=True,
                    is_error=failed > 0,
                    duration_s=elapsed,
                    raw=event,
                )
            ]

        if etype == "level_start":
            level = event.get("level", "?")
            steps = event.get("steps", [])
            return [
                LogEvent(
                    kind="system",
                    summary=f"[level_start] level={level} steps={', '.join(steps)}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "level_complete":
            level = event.get("level", "?")
            elapsed = event.get("elapsed_s", 0)
            return [
                LogEvent(
                    kind="system",
                    summary=f"[level_complete] level={level} {elapsed:.0f}s",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "step_start":
            step_id = event.get("step_id", "?")
            mode = event.get("mode", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[step_start] {step_id} mode={mode}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "step_complete":
            step_id = event.get("step_id", "?")
            elapsed = event.get("elapsed_s", 0)
            return [
                LogEvent(
                    kind="system",
                    summary=f"[step_complete] {step_id} {elapsed:.0f}s",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "step_fail":
            step_id = event.get("step_id", "?")
            elapsed = event.get("elapsed_s", 0)
            error = event.get("error", "")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[step_fail] {step_id} {elapsed:.0f}s {error}",
                    adapter="process",
                    timestamp=ts,
                    is_error=True,
                    raw=event,
                )
            ]

        if etype == "step_skip":
            step_id = event.get("step_id", "?")
            reason = event.get("reason", "")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[step_skip] {step_id} {reason}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "step_blocked":
            step_id = event.get("step_id", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[step_blocked] {step_id}",
                    adapter="process",
                    timestamp=ts,
                    is_error=True,
                    raw=event,
                )
            ]

        if etype == "worker_dispatch":
            step_id = event.get("step_id", "?")
            num_workers = event.get("num_workers", "?")
            items_count = event.get("items_count", "?")
            return [
                LogEvent(
                    kind="system",
                    summary=f"[worker_dispatch] {step_id} workers={num_workers} items={items_count}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "item_start":
            step_id = event.get("step_id", "?")
            item_key = event.get("item_key", "?")
            worker_id = event.get("worker_id")
            worker_suffix = f" worker={worker_id}" if worker_id else ""
            return [
                LogEvent(
                    kind="system",
                    summary=f"[item_start] {step_id}/{item_key}{worker_suffix}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "item_complete":
            step_id = event.get("step_id", "?")
            item_key = event.get("item_key", "?")
            elapsed = event.get("elapsed_s")
            elapsed_suffix = f" {elapsed:.1f}s" if isinstance(elapsed, (int, float)) else ""
            return [
                LogEvent(
                    kind="system",
                    summary=f"[item_complete] {step_id}/{item_key}{elapsed_suffix}",
                    adapter="process",
                    timestamp=ts,
                    raw=event,
                )
            ]

        if etype == "item_fail":
            step_id = event.get("step_id", "?")
            item_key = event.get("item_key", "?")
            error = event.get("error", "")
            failure_class = event.get("failure_class")
            cls_suffix = f" [{failure_class}]" if failure_class else ""
            return [
                LogEvent(
                    kind="system",
                    summary=f"[item_fail] {step_id}/{item_key}{cls_suffix} {error}",
                    adapter="process",
                    timestamp=ts,
                    is_error=True,
                    raw=event,
                )
            ]

        return [
            LogEvent(
                kind="system",
                summary=f"[{etype or 'unknown'}] {line.strip()[:80]}",
                adapter="process",
                timestamp=ts,
                raw=event,
            )
        ]

    def flush(self) -> list[LogEvent]:
        return []


# ── Codex parser (codex-cli 0.124.0 flat envelope) ──────────────


class CodexLogParser:
    """Parser for codex-cli 0.124.0 JSONL output.

    codex-cli 0.124.0 uses a flat top-level ``type`` envelope with these
    events: ``thread.started``, ``turn.started``,
    ``item.started``/``item.updated``/``item.completed`` (each with an
    ``item.item_type`` of ``agent_message`` / ``reasoning`` /
    ``command_execution`` / ``file_change`` / ``mcp_tool_call`` /
    ``web_search`` / ``todo_list``), ``turn.completed`` (success
    terminal; carries ``usage`` inline), ``turn.failed`` (failure
    terminal), and intermediate ``error`` events (in-process reconnects,
    not terminal).
    """

    adapter_name: str = "codex"

    def parse_line(self, line: str) -> list[LogEvent]:
        event = _parse_json(line)
        if event is None:
            return [
                LogEvent(
                    kind="raw",
                    summary=f"[raw] {line.strip()}",
                    adapter=self.adapter_name,
                    raw=line,
                )
            ]

        etype = event.get("type", "unknown")
        ts = event.get("timestamp")

        if etype == "thread.started":
            thread_id = str(event.get("thread_id", "?"))
            return [
                LogEvent(
                    kind="init",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[init] thread={thread_id[:8]}",
                    raw=event,
                )
            ]

        if etype == "turn.started":
            return [
                LogEvent(
                    kind="system",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary="[turn.started]",
                    raw=event,
                )
            ]

        if etype in ("item.started", "item.updated", "item.completed"):
            return self._parse_item(event, etype, ts)

        if etype == "turn.completed":
            return self._parse_terminal(event, ts, is_error=False)

        if etype == "turn.failed":
            return self._parse_terminal(event, ts, is_error=True)

        if etype == "error":
            # Intermediate reconnect noise — flag but don't mark terminal.
            return [
                LogEvent(
                    kind="system",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    is_error=False,
                    summary=f"[reconnect] {event.get('message', '')}",
                    raw=event,
                )
            ]

        return [
            LogEvent(
                kind=etype if isinstance(etype, str) else "unknown",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[{etype}] {event}",
                raw=event,
            )
        ]

    def flush(self) -> list[LogEvent]:
        return []

    def _parse_item(self, event: dict[str, Any], etype: str, ts: str | None) -> list[LogEvent]:
        item_raw: Any = event.get("item")
        if not isinstance(item_raw, dict):
            return [
                LogEvent(
                    kind="system",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[{etype}] {event}",
                    raw=event,
                )
            ]
        item = cast(dict[str, Any], item_raw)
        item_type = str(item.get("item_type", "unknown"))

        # Only surface completed items for the main timeline; started/updated
        # produce a lot of streaming noise that the compactor can drop later.
        if etype != "item.completed":
            return []

        if item_type == "reasoning":
            text = str(item.get("text", ""))
            if not text.strip():
                return []
            return [
                LogEvent(
                    kind="thinking",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[thinking] {text}",
                    raw=event,
                )
            ]

        if item_type == "agent_message":
            text = str(item.get("text", ""))
            return [
                LogEvent(
                    kind="text",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[text] {text}",
                    raw=event,
                )
            ]

        if item_type == "command_execution":
            cmd = str(item.get("command", "?"))
            return [
                LogEvent(
                    kind="tool_call",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[call:exec] {cmd}",
                    raw=event,
                )
            ]

        if item_type == "file_change":
            path_value = str(item.get("path", "?"))
            return [
                LogEvent(
                    kind="tool_call",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[call:file_change] {path_value}",
                    raw=event,
                )
            ]

        if item_type == "mcp_tool_call":
            name = str(item.get("tool_name", "?"))
            return [
                LogEvent(
                    kind="tool_call",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[call:mcp:{name}]",
                    raw=event,
                )
            ]

        if item_type == "web_search":
            query = str(item.get("query", "?"))
            return [
                LogEvent(
                    kind="tool_call",
                    adapter=self.adapter_name,
                    timestamp=ts,
                    summary=f"[call:web_search] {query}",
                    raw=event,
                )
            ]

        return [
            LogEvent(
                kind="tool_call",
                adapter=self.adapter_name,
                timestamp=ts,
                summary=f"[call:{item_type}]",
                raw=event,
            )
        ]

    def _parse_terminal(
        self, event: dict[str, Any], ts: str | None, *, is_error: bool
    ) -> list[LogEvent]:
        stats = sum_codex_usage(event)
        if is_error:
            error_info: Any = event.get("error", {})
            message = (
                str(cast(dict[str, Any], error_info).get("message", ""))
                if isinstance(error_info, dict)
                else str(error_info)
            )
            summary = f"[result] FAIL {message}"
        else:
            summary = (
                f"[result] DONE input={stats.input_tokens} "
                f"output={stats.output_tokens} "
                f"cached={stats.cache_read_tokens}"
            )
        return [
            LogEvent(
                kind="result",
                adapter=self.adapter_name,
                timestamp=ts,
                is_error=is_error,
                is_done=True,
                summary=summary,
                raw=event,
            )
        ]


# ── Auto-detection ───────────────────────────────────────────────


def detect_adapter(lines: list[str]) -> str:
    """Detect adapter type from the first few lines of a JSONL file.

    Returns "claude", "gemini", "pi", "codex", "runpool", "process", or "unknown".
    """
    for line in lines[:20]:
        event = _parse_json(line)
        if event is None:
            stripped = line.strip()
            if stripped.startswith(("YOLO mode", "Both GOOGLE_API_KEY")):
                return "gemini"
            continue

        # RunPool events use "event" key, not "type"
        if event.get("event") == "pool_start":
            return "runpool"

        # Process orchestrator events
        event_name = event.get("event")
        if event_name in {
            "process_start",
            "process_complete",
            "level_start",
            "level_complete",
            "step_start",
            "step_complete",
            "step_fail",
            "step_skip",
            "step_blocked",
            "worker_dispatch",
            "item_start",
            "item_complete",
            "item_fail",
        } and ("process" in event or "step_id" in event or str(event_name).startswith("item_")):
            return "process"

        etype = event.get("type", "")

        # Codex events (codex-cli 0.124.0 flat envelope). thread.started +
        # thread_id or the turn.* / item.* top-level types are distinctive.
        if etype == "thread.started" and "thread_id" in event:
            return "codex"
        if etype in ("turn.started", "turn.completed", "turn.failed"):
            return "codex"
        if etype in ("item.started", "item.updated", "item.completed") and isinstance(
            event.get("item"), dict
        ):
            return "codex"

        # Pi events
        if etype == "session" and "version" in event and "cwd" in event:
            return "pi"
        if etype == "agent_start":
            return "pi"
        if etype in ("tool_execution_start", "tool_execution_end", "tool_execution_update"):
            return "pi"

        if etype == "system" and event.get("subtype") in (
            "init",
            "hook_started",
            "hook_response",
        ):
            return "claude"

        if etype == "init" and "session_id" in event and "model" in event:
            return "gemini"

        if etype == "assistant" and isinstance(event.get("message", {}).get("content"), list):
            return "claude"

        if etype == "message" and "role" in event:
            return "gemini"

        if etype == "tool_use" and "tool_name" in event:
            return "gemini"

    return "unknown"


def create_parser(
    adapter: str,
) -> (
    ClaudeLogParser
    | CodexLogParser
    | GeminiLogParser
    | PiLogParser
    | RunPoolLogParser
    | ProcessLogParser
):
    """Create a parser instance for the given adapter name."""
    if adapter == "claude":
        return ClaudeLogParser()
    if adapter == "codex":
        return CodexLogParser()
    if adapter == "gemini":
        return GeminiLogParser()
    if adapter == "pi":
        return PiLogParser()
    if adapter == "runpool":
        return RunPoolLogParser()
    if adapter == "process":
        return ProcessLogParser()
    return ClaudeLogParser()


# ── Renderer ─────────────────────────────────────────────────────

COLORS = [
    "\033[36m",  # cyan
    "\033[33m",  # yellow
    "\033[32m",  # green
    "\033[35m",  # magenta
    "\033[34m",  # blue
    "\033[91m",  # bright red
    "\033[96m",  # bright cyan
    "\033[93m",  # bright yellow
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
COLOR_DONE = "\033[32;1m"  # bold green
COLOR_FAIL = "\033[31;1m"  # bold red


def colorize(code: str, text: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{RESET}"


def render_log_event(event: LogEvent, use_color: bool) -> str:
    """Render a LogEvent's summary with optional color highlighting."""
    text = event.summary
    if event.is_done and event.is_error:
        return colorize(COLOR_FAIL, text, use_color)
    if event.is_done:
        return colorize(COLOR_DONE, text, use_color)
    return text


# ── LogFile (file watcher) ───────────────────────────────────────


class LogFile:
    """Tracks read offset and metadata for one JSONL file, with parser integration."""

    def __init__(self, path: Path, color_idx: int) -> None:
        self.path: Path = path
        self.offset: int = 0
        self.color_idx: int = color_idx
        self.done: bool = False
        self.is_error: bool = False
        self.cost_usd: float | None = None
        self.duration_s: float | None = None
        self.model: str | None = None
        self.output_tokens: int | None = None
        self.input_tokens: int | None = None
        self.tool_calls: int | None = None
        self.models_used: list[str] = []
        self.error_message: str | None = None
        self.usage_stats: UsageStats | None = None
        self.rate_limit_events: list[LogEvent] = []
        self.parser: LogParser | None = None
        self._first_lines: list[str] = []
        self._parse_name()

    def _parse_name(self) -> None:
        """Extract step-id and context from filename."""
        stem = logical_path(self.path).stem
        parts = stem.split("_", 2)
        if len(parts) >= 2:
            self.step_id: str = parts[0]
            self.context: str = parts[1]
        else:
            self.step_id = stem
            self.context = ""

    @property
    def label(self) -> str:
        if self.context:
            return f"{self.step_id}/{self.context}"
        return self.step_id

    def read_new_events(self) -> list[LogEvent]:
        """Read new bytes from the file, parse through the adapter parser."""
        try:
            artifact = ArtifactPath(self.path)
            size = artifact.logical_size
        except OSError:
            log.debug("Failed to stat %s", self.path, exc_info=True)
            return []

        if size <= self.offset:
            if size < self.offset:
                self.offset = 0
            return []

        try:
            with ArtifactPath(self.path).open_text() as f:
                f.seek(self.offset)
                raw = f.read()
                self.offset = f.tell()
        except OSError:
            return []

        raw_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

        # Auto-detect parser on first read
        if self.parser is None:
            self._first_lines.extend(raw_lines)
            adapter = detect_adapter(self._first_lines)
            self.parser = create_parser(adapter)
            events: list[LogEvent] = []
            for line in self._first_lines:
                events.extend(self.parser.parse_line(line))
            self._first_lines.clear()
            self._track_results(events)
            return events

        events = []
        for line in raw_lines:
            events.extend(self.parser.parse_line(line))
        self._track_results(events)
        return events

    def _track_results(self, events: list[LogEvent]) -> None:
        """Capture cost/duration/model/tokens from init and result events."""
        for ev in events:
            raw = ev.raw if isinstance(ev.raw, dict) else {}

            if ev.kind == "rate_limit":
                self.rate_limit_events.append(ev)
                continue

            if ev.kind == "init":
                model = raw.get("model")
                if model:
                    self.model = model

            if ev.is_done:
                self.done = True
                self.is_error = ev.is_error
                if ev.cost_usd is not None:
                    self.cost_usd = ev.cost_usd
                if ev.duration_s is not None:
                    self.duration_s = ev.duration_s

                usage: dict[str, Any] = raw.get("usage", {})
                if usage:
                    self.output_tokens = usage.get("output_tokens")
                    self.input_tokens = usage.get("input_tokens", 0) + usage.get(
                        "cache_read_input_tokens", 0
                    )

                stats: dict[str, Any] = raw.get("stats", {})
                if stats:
                    self.output_tokens = self.output_tokens or stats.get("output_tokens")
                    self.input_tokens = self.input_tokens or stats.get("input_tokens")
                    self.tool_calls = stats.get("tool_calls")
                    models: dict[str, Any] = stats.get("models", {})
                    if models:
                        self.models_used = list(models.keys())

                if ev.is_error:
                    error: Any = raw.get("error", "")
                    if isinstance(error, dict):
                        err_dict: dict[str, Any] = cast(dict[str, Any], error)
                        self.error_message = err_dict.get("message", str(err_dict))
                    elif error:
                        self.error_message = str(error)[:200]

                # Populate structured UsageStats from adapter-specific data.
                self._track_usage_stats(ev, raw)

    def _track_usage_stats(self, ev: LogEvent, raw: dict[str, Any]) -> None:
        """Build structured UsageStats from adapter-specific result data."""
        adapter = ev.adapter

        if adapter == "pi" and raw.get("type") == "agent_end":
            self.usage_stats = sum_pi_usage(raw)
            self.usage_stats.duration_s = ev.duration_s or 0.0
            self.usage_stats.steps = 1
        elif adapter == "gemini":
            stats_dict = raw.get("stats", {})
            if stats_dict:
                entries = extract_gemini_usage(stats_dict)
                us = UsageStats(steps=1)
                for entry in entries:
                    us += entry
                us.tool_calls = stats_dict.get("tool_calls", 0)
                dur_ms = stats_dict.get("duration_ms")
                us.duration_s = dur_ms / 1000 if dur_ms else (ev.duration_s or 0.0)
                us.cost_is_estimated = True
                self.usage_stats = us
        elif adapter == "claude":
            # Shared with the trace extractor so both planes report the same
            # totals for the same file: modelUsage covers the whole attempt
            # tree including subagents, while top-level `usage` does not.
            attempt_usage = claude_attempt_usage(raw)
            self.usage_stats = UsageStats(
                input_tokens=attempt_usage.input_tokens,
                output_tokens=attempt_usage.output_tokens,
                cache_read_tokens=attempt_usage.cache_read_tokens,
                cache_write_tokens=attempt_usage.cache_write_tokens,
                cost_usd=ev.cost_usd or 0.0,
                # Claude Code computes total_cost_usd locally from a bundled
                # price table; Anthropic documents it as an estimate and says
                # not to make billing decisions from it. Publishing it as
                # actual spend is the defect this flag prevents.
                cost_is_estimated=True,
                duration_s=ev.duration_s or 0.0,
                model=self.model or "",
                provider="anthropic",
                steps=1,
            )
        elif adapter == "codex" and raw.get("type") == "turn.completed":
            # codex-cli 0.124.0 carries usage inline on the terminal
            # turn.completed event; sum_codex_usage maps cached_input_tokens
            # → cache_read_tokens and pins provider="openai".
            self.usage_stats = sum_codex_usage(raw)
            self.usage_stats.duration_s = ev.duration_s or 0.0
            self.usage_stats.model = self.model or ""
            self.usage_stats.cost_is_estimated = True
            self.usage_stats.steps = 1

    def flush(self) -> list[LogEvent]:
        """Flush any buffered parser state."""
        if self.parser is not None:
            return self.parser.flush()
        return []


# ── File discovery ───────────────────────────────────────────────


def discover_files(
    dirs: list[Path],
    known: dict[Path, LogFile],
    color_counter: list[int],
) -> None:
    """Scan directories for new .jsonl files."""
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            for entry in os.scandir(d):
                if entry.name.endswith(".jsonl") and entry.is_file():
                    p = Path(entry.path)
                    if p not in known:
                        lf = LogFile(p, color_counter[0] % len(COLORS))
                        color_counter[0] += 1
                        known[p] = lf
        except OSError:
            log.debug("Failed to scan directory for JSONL files", exc_info=True)


# ── Status line ──────────────────────────────────────────────────


def render_status_line(files: dict[Path, LogFile], use_color: bool) -> str:
    """Render a sticky status line for stderr."""
    active = sum(1 for f in files.values() if not f.done)
    done = sum(1 for f in files.values() if f.done)
    return colorize(DIM, f"[tail] {len(files)} files ({active} active, {done} done)", use_color)
