"""Tests for metaproc.logutil.parsing — unified log parsing."""

from __future__ import annotations

import json
from pathlib import Path

from metaproc.logutil.parsing import (
    ClaudeLogParser,
    CodexLogParser,
    GeminiLogParser,
    LogEvent,
    LogFile,
    PiLogParser,
    RunPoolLogParser,
    colorize,
    create_parser,
    detect_adapter,
    discover_files,
    render_log_event,
    render_status_line,
)

# ── LogEvent ─────────────────────────────────────────────────────


class TestLogEvent:
    def test_defaults(self):
        ev = LogEvent(kind="text", summary="hello")
        assert ev.adapter == "unknown"
        assert ev.is_error is False
        assert ev.is_done is False
        assert ev.cost_usd is None
        assert ev.duration_s is None
        assert ev.raw == {}

    def test_all_fields(self):
        ev = LogEvent(
            kind="result",
            summary="[DONE]",
            adapter="claude",
            timestamp="2025-01-01",
            is_done=True,
            cost_usd=0.05,
            duration_s=12.3,
        )
        assert ev.is_done is True
        assert ev.cost_usd == 0.05


# ── ClaudeLogParser ──────────────────────────────────────────────


class TestClaudeLogParser:
    def test_non_json_line(self):
        parser = ClaudeLogParser()
        events = parser.parse_line("not json at all")
        assert len(events) == 1
        assert events[0].kind == "raw"
        assert events[0].adapter == "claude"

    def test_system_init(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "abcd1234efgh",
                "model": "opus",
                "tools": ["Read", "Write", "Bash"],
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "init"
        assert "abcd1234" in events[0].summary
        assert "opus" in events[0].summary
        assert "tools=3" in events[0].summary

    def test_system_hook_suppressed(self):
        parser = ClaudeLogParser()
        for subtype in ("hook_started", "hook_response"):
            line = json.dumps({"type": "system", "subtype": subtype})
            events = parser.parse_line(line)
            assert events == []

    def test_rate_limit_event_emits_rate_limit_event(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "blocked",
                    "rateLimitType": "five_hour",
                    "resetsAt": 1774501200,
                },
                "uuid": "abc123",
                "session_id": "sess-1",
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "rate_limit"
        assert ev.adapter == "claude"
        assert ev.provider == "anthropic"
        assert ev.is_error is True
        assert "blocked" in ev.summary
        assert "five_hour" in ev.summary

    def test_rate_limit_event_allowed_is_not_error(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "rate_limit"
        assert events[0].is_error is False

    def test_assistant_tool_use(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu-read-1",
                            "name": "Read",
                            "input": {"file_path": "/foo/bar.py"},
                        }
                    ]
                },
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_call"
        assert "Read" in events[0].summary
        assert events[0].tool_name == "Read"
        assert events[0].invocation_id == "toolu-read-1"

    def test_assistant_text(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello world"}]},
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "text"
        assert "Hello world" in events[0].summary

    def test_assistant_thinking_suppressed(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "text": "hmm"}]},
            }
        )
        events = parser.parse_line(line)
        assert events == []

    def test_user_tool_result(self):
        parser = ClaudeLogParser()
        parser.parse_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu-read-1",
                                "name": "Read",
                                "input": {},
                            }
                        ]
                    },
                }
            )
        )
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu-read-1",
                            "is_error": False,
                            "content": "file contents here",
                        }
                    ]
                },
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert "[result]" in events[0].summary
        assert events[0].tool_name == "Read"
        assert events[0].invocation_id == "toolu-read-1"
        # Char count is in summary_parts (omitted entry) when structured rendering is available
        if events[0].summary_parts:
            omitted = [p for p in events[0].summary_parts if p.get("type") == "omitted"]
            assert any("18 chars" in p["text"] for p in omitted)

    def test_user_tool_result_error(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "is_error": True,
                            "content": "error!",
                        }
                    ]
                },
            }
        )
        events = parser.parse_line(line)
        assert events[0].is_error is True
        assert "ERROR" in events[0].summary

    def test_result_success(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "result",
                "is_error": False,
                "cost_usd": 0.12,
                "duration_ms": 5000,
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "result"
        assert events[0].is_done is True
        assert events[0].is_error is False
        assert events[0].cost_usd == 0.12
        assert events[0].duration_s == 5.0
        assert "[DONE]" in events[0].summary
        assert "$0.12" in events[0].summary

    def test_result_failure(self):
        parser = ClaudeLogParser()
        line = json.dumps(
            {
                "type": "result",
                "is_error": True,
                "error": "out of tokens",
                "duration_s": 30,
            }
        )
        events = parser.parse_line(line)
        assert events[0].is_done is True
        assert events[0].is_error is True
        assert "[FAIL]" in events[0].summary
        assert "out of tokens" in events[0].summary

    def test_flush_is_noop(self):
        parser = ClaudeLogParser()
        assert parser.flush() == []

    def test_unknown_event_type(self):
        parser = ClaudeLogParser()
        events = parser.parse_line(json.dumps({"type": "custom_event"}))
        assert len(events) == 1
        assert events[0].kind == "custom_event"


# ── GeminiLogParser ──────────────────────────────────────────────


class TestGeminiLogParser:
    def test_init_event(self):
        parser = GeminiLogParser()
        line = json.dumps(
            {"type": "init", "session_id": "sess12345678", "model": "gemini-3.5-flash"}
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "init"
        assert "sess1234" in events[0].summary
        assert "gemini-3.5-flash" in events[0].summary

    def test_tool_use(self):
        parser = GeminiLogParser()
        line = json.dumps(
            {
                "type": "tool_use",
                "tool_id": "gemini-tool-1",
                "tool_name": "shell",
                "parameters": {"command": "ls -la"},
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_call"
        assert "shell" in events[0].summary
        assert events[0].tool_name == "shell"
        assert events[0].invocation_id == "gemini-tool-1"

    def test_tool_result_success(self):
        parser = GeminiLogParser()
        line = json.dumps(
            {
                "type": "tool_result",
                "tool_id": "t1",
                "status": "success",
                "output": "ok",
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert events[0].is_error is False
        assert events[0].invocation_id == "t1"

    def test_tool_result_error(self):
        parser = GeminiLogParser()
        line = json.dumps(
            {
                "type": "tool_result",
                "tool_id": "t1",
                "status": "error",
                "output": "failed",
            }
        )
        events = parser.parse_line(line)
        assert events[0].is_error is True

    def test_delta_coalescing(self):
        parser = GeminiLogParser()
        # Send delta chunks — should be buffered
        for chunk in ["Hello ", "world", "!"]:
            events = parser.parse_line(
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "delta": True,
                        "content": chunk,
                    }
                )
            )
            assert events == []

        # Flush should coalesce
        events = parser.flush()
        assert len(events) == 1
        assert events[0].kind == "text"
        assert "Hello world!" in events[0].summary

    def test_delta_flushed_on_non_delta(self):
        parser = GeminiLogParser()
        parser.parse_line(
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "delta": True,
                    "content": "buffered",
                }
            )
        )
        # A non-delta event should flush the buffer first
        events = parser.parse_line(
            json.dumps({"type": "init", "session_id": "x" * 10, "model": "test"})
        )
        assert len(events) == 2
        assert events[0].kind == "text"
        assert "buffered" in events[0].summary
        assert events[1].kind == "init"

    def test_user_message(self):
        parser = GeminiLogParser()
        events = parser.parse_line(
            json.dumps({"type": "message", "role": "user", "content": "do stuff"})
        )
        assert len(events) == 1
        assert events[0].kind == "text"

    def test_noise_suppressed(self):
        parser = GeminiLogParser()
        events = parser.parse_line("some random stderr output")
        assert events == []

    def test_noise_kept_for_errors(self):
        parser = GeminiLogParser()
        events = parser.parse_line("Error: something went wrong")
        assert len(events) == 1
        assert events[0].kind == "raw"

    def test_result_success(self):
        parser = GeminiLogParser()
        line = json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {"duration_ms": 10000, "total_tokens": 5000, "tool_calls": 3},
            }
        )
        events = parser.parse_line(line)
        assert len(events) == 1
        assert events[0].is_done is True
        assert events[0].is_error is False
        assert "[DONE]" in events[0].summary
        assert "tokens=5,000" in events[0].summary
        assert "tools=3" in events[0].summary

    def test_result_error(self):
        parser = GeminiLogParser()
        events = parser.parse_line(
            json.dumps({"type": "result", "status": "error", "error": "timeout"})
        )
        assert events[0].is_error is True
        assert "[FAIL]" in events[0].summary


# ── Pi CLI parser ────────────────────────────────────────────────


class TestPiLogParser:
    def setup_method(self):
        self.parser = PiLogParser()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_adapter_name(self):
        assert self.parser.adapter_name == "pi"

    def test_session_event(self):
        line = json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "12d00295-b778-4ae2-8eee-0cd5f512a02c",
                "timestamp": "2026-04-01T01:18:58.441Z",
                "cwd": "/workspace/project",
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "init"
        assert events[0].adapter == "pi"
        assert "12d00295" in events[0].summary

    def test_agent_start(self):
        line = json.dumps({"type": "agent_start", "model": "sonnet"})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "init"
        assert events[0].adapter == "pi"
        assert "sonnet" in events[0].summary

    def test_tool_execution_start_with_toolname_field(self):
        """Pi JSON mode uses toolName and args fields."""
        line = json.dumps(
            {
                "type": "tool_execution_start",
                "toolCallId": "toolu_01GUECqtjhqkVjupwJYhdqr8",
                "toolName": "read",
                "args": {"path": "example_plugin/process/predict/predict-ticker.runbook.md"},
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_call"
        assert "read" in events[0].summary
        assert "path=" in events[0].summary
        assert events[0].summary_parts is not None
        assert events[0].tool_name == "read"
        assert events[0].invocation_id == "toolu_01GUECqtjhqkVjupwJYhdqr8"

    def test_tool_execution_start_with_bash_command(self):
        line = json.dumps(
            {
                "type": "tool_execution_start",
                "toolCallId": "toolu_0189e6cWeQvgt94zkifczSjX",
                "toolName": "bash",
                "args": {"command": "ls -la"},
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert "bash" in events[0].summary
        assert "command=" in events[0].summary

    def test_tool_execution_start_legacy_tool_field(self):
        """Backward compat: older Pi versions may use 'tool' field."""
        line = json.dumps({"type": "tool_execution_start", "tool": "bash"})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_call"
        assert "bash" in events[0].summary

    def test_tool_execution_end_with_result(self):
        line = json.dumps(
            {
                "type": "tool_execution_end",
                "toolCallId": "t1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "file1.py\nfile2.py\n"}]},
                "isError": False,
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert "bash" in events[0].summary
        assert "file1.py" in events[0].summary
        assert events[0].is_error is False
        assert events[0].summary_parts is not None
        assert events[0].tool_name == "bash"
        assert events[0].invocation_id == "t1"

    def test_tool_execution_end_error(self):
        line = json.dumps(
            {
                "type": "tool_execution_end",
                "toolName": "read",
                "result": {"content": [{"type": "text", "text": "No such file"}]},
                "isError": True,
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].is_error is True
        assert "ERROR" in events[0].summary
        assert "No such file" in events[0].summary

    def test_tool_execution_end_no_result(self):
        line = json.dumps({"type": "tool_execution_end", "toolName": "write"})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert "write" in events[0].summary

    def test_message_update(self):
        line = json.dumps({"type": "message_update", "content": "Writing file..."})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "text"

    def test_agent_end(self):
        line = json.dumps({"type": "agent_end", "messages": []})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "result"
        assert events[0].is_done is True

    def test_silent_events(self):
        for etype in ("turn_start", "turn_end", "message_start", "tool_execution_update"):
            line = json.dumps({"type": etype})
            events = self.parser.parse_line(line)
            assert events == []

    def test_message_end_no_content(self):
        line = json.dumps({"type": "message_end", "message": {"content": []}})
        events = self.parser.parse_line(line)
        assert events == []

    def test_raw_non_json(self):
        events = self.parser.parse_line("some plain text output")
        assert len(events) == 1
        assert events[0].kind == "raw"

    def test_empty_line(self):
        assert self.parser.parse_line("") == []

    def test_timestamp_from_session_iso(self):
        line = json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "abc-123",
                "timestamp": "2026-04-01T07:06:37.199Z",
                "cwd": "/tmp",
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].timestamp == "2026-04-01T07:06:37.199Z"

    def test_timestamp_from_message_epoch_ms(self):
        line = json.dumps(
            {
                "type": "message_update",
                "content": "hello",
                "message": {"timestamp": 1775027197205},
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].timestamp is not None
        assert "2026-" in events[0].timestamp  # epoch ms maps to 2026

    def test_full_pi_session_sequence(self):
        """Parse a realistic sequence of Pi JSON mode events."""
        lines = [
            json.dumps(
                {
                    "type": "session",
                    "version": 3,
                    "id": "abc-123",
                    "timestamp": "2026-04-01T01:00:00Z",
                    "cwd": "/tmp",
                }
            ),
            json.dumps({"type": "agent_start"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "message_start"}),
            json.dumps({"type": "message_update", "content": "Reading the runbook..."}),
            json.dumps({"type": "message_end"}),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "t1",
                    "toolName": "read",
                    "args": {"path": "runbook.md"},
                }
            ),
            json.dumps({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "read"}),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "t2",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                }
            ),
            json.dumps({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "bash"}),
            json.dumps({"type": "turn_end"}),
            json.dumps({"type": "agent_end"}),
        ]
        all_events = []
        for line in lines:
            all_events.extend(self.parser.parse_line(line))

        kinds = [e.kind for e in all_events]
        assert kinds.count("init") == 2  # session + agent_start
        assert kinds.count("tool_call") == 2
        assert kinds.count("tool_result") == 2
        assert kinds.count("text") == 1
        assert kinds.count("result") == 1
        assert all_events[-1].is_done is True


class TestCodexLogParser:
    def test_completed_tool_item_is_one_completed_invocation(self):
        parser = CodexLogParser()

        events = parser.parse_line(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item-42",
                        "type": "command_execution",
                        "command": "echo ok",
                        "status": "completed",
                        "exit_code": 0,
                    },
                }
            )
        )

        assert [event.kind for event in events] == ["tool_call", "tool_result"]
        assert {event.tool_name for event in events} == {"exec"}
        assert {event.invocation_id for event in events} == {"item-42"}

    def test_failed_completed_item_marks_only_the_result_as_error(self):
        parser = CodexLogParser()

        events = parser.parse_line(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item-43",
                        "type": "command_execution",
                        "command": "false",
                        "status": "failed",
                        "exit_code": 1,
                    },
                }
            )
        )

        assert [event.is_error for event in events] == [False, True]


# ── RunPoolLogParser ────────────────────────────────────────────


class TestRunPoolLogParser:
    parser: RunPoolLogParser  # pyright: ignore[reportUninitializedInstanceVariable]

    def setup_method(self) -> None:
        self.parser = RunPoolLogParser()

    def test_pool_start(self):
        line = json.dumps(
            {
                "event": "pool_start",
                "pool_id": "pool-test",
                "backend": "local",
                "max_concurrency": 10,
                "ts": "2026-04-06T10:00:00+00:00",
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "init"
        assert "pool-test" in events[0].summary
        assert events[0].timestamp == "2026-04-06T10:00:00+00:00"

    def test_process_start(self):
        line = json.dumps(
            {
                "event": "process_start",
                "pid": 123,
                "label": "ITEM=A",
                "ts": "2026-04-06T10:00:01+00:00",
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "system"
        assert "123" in events[0].summary
        assert "ITEM=A" in events[0].summary

    def test_process_exit_success(self):
        line = json.dumps(
            {
                "event": "process_exit",
                "pid": 123,
                "exit_code": 0,
                "label": "ITEM=A",
                "ts": "2026-04-06T10:00:30+00:00",
            }
        )
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].is_error is False

    def test_process_exit_failure(self):
        line = json.dumps({"event": "process_exit", "pid": 123, "exit_code": 1, "label": "ITEM=A"})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].is_error is True

    def test_pressure_check(self):
        line = json.dumps({"event": "pressure_check", "level": "elevated", "available_pct": 35.0})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert "elevated" in events[0].summary
        assert "35" in events[0].summary

    def test_pool_shutdown(self):
        line = json.dumps({"event": "pool_shutdown", "completed": 100, "failed": 2, "killed": 0})
        events = self.parser.parse_line(line)
        assert len(events) == 1
        assert events[0].kind == "result"
        assert events[0].is_done is True
        assert events[0].is_error is True  # failed > 0


# ── detect_adapter ───────────────────────────────────────────────


class TestDetectAdapter:
    def test_claude_system_init(self):
        lines = [json.dumps({"type": "system", "subtype": "init"})]
        assert detect_adapter(lines) == "claude"

    def test_gemini_init(self):
        lines = [json.dumps({"type": "init", "session_id": "x", "model": "y"})]
        assert detect_adapter(lines) == "gemini"

    def test_claude_assistant(self):
        lines = [json.dumps({"type": "assistant", "message": {"content": [{"type": "text"}]}})]
        assert detect_adapter(lines) == "claude"

    def test_gemini_message(self):
        lines = [json.dumps({"type": "message", "role": "user"})]
        assert detect_adapter(lines) == "gemini"

    def test_gemini_noise(self):
        lines = ["YOLO mode enabled"]
        assert detect_adapter(lines) == "gemini"

    def test_pi_session_event(self):
        lines = [json.dumps({"type": "session", "version": 3, "id": "abc", "cwd": "/tmp"})]
        assert detect_adapter(lines) == "pi"

    def test_pi_agent_start(self):
        lines = [json.dumps({"type": "agent_start", "model": "sonnet"})]
        assert detect_adapter(lines) == "pi"

    def test_pi_tool_execution(self):
        lines = [json.dumps({"type": "tool_execution_start", "tool": "bash"})]
        assert detect_adapter(lines) == "pi"

    def test_runpool_pool_start(self):
        lines = [
            json.dumps(
                {
                    "event": "pool_start",
                    "pool_id": "pool-20260406T090755Z",
                    "backend": "local",
                    "max_concurrency": 20,
                    "ts": "2026-04-06T09:07:55+00:00",
                }
            )
        ]
        assert detect_adapter(lines) == "runpool"

    def test_unknown(self):
        lines = ["not json", "also not json"]
        assert detect_adapter(lines) == "unknown"


# ── create_parser ────────────────────────────────────────────────


class TestCreateParser:
    def test_claude(self):
        assert isinstance(create_parser("claude"), ClaudeLogParser)

    def test_gemini(self):
        assert isinstance(create_parser("gemini"), GeminiLogParser)

    def test_pi(self):
        assert isinstance(create_parser("pi"), PiLogParser)

    def test_runpool(self):
        assert isinstance(create_parser("runpool"), RunPoolLogParser)

    def test_unknown_defaults_to_claude(self):
        assert isinstance(create_parser("unknown"), ClaudeLogParser)


# ── Renderer ─────────────────────────────────────────────────────


class TestRenderer:
    def test_colorize_enabled(self):
        result = colorize("\033[32m", "text", use_color=True)
        assert "\033[32m" in result
        assert "\033[0m" in result

    def test_colorize_disabled(self):
        result = colorize("\033[32m", "text", use_color=False)
        assert result == "text"

    def test_render_done_success(self):
        ev = LogEvent(kind="result", summary="[DONE]", is_done=True)
        result = render_log_event(ev, use_color=True)
        assert "\033[32;1m" in result

    def test_render_done_error(self):
        ev = LogEvent(kind="result", summary="[FAIL]", is_done=True, is_error=True)
        result = render_log_event(ev, use_color=True)
        assert "\033[31;1m" in result

    def test_render_normal(self):
        ev = LogEvent(kind="text", summary="[text] hello")
        result = render_log_event(ev, use_color=True)
        assert result == "[text] hello"


# ── LogFile ──────────────────────────────────────────────────────


class TestLogFile:
    def test_parse_name(self):
        lf = LogFile(Path("/tmp/scaffold_AAPL_2025.jsonl"), 0)
        assert lf.step_id == "scaffold"
        assert lf.context == "AAPL"

    def test_parse_name_no_underscore(self):
        lf = LogFile(Path("/tmp/mylog.jsonl"), 0)
        assert lf.step_id == "mylog"
        assert lf.context == ""

    def test_label(self):
        lf = LogFile(Path("/tmp/scaffold_AAPL.jsonl"), 0)
        assert lf.label == "scaffold/AAPL"

        lf2 = LogFile(Path("/tmp/mylog.jsonl"), 0)
        assert lf2.label == "mylog"

    def test_read_new_events_from_file(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "abc",
                    "model": "opus",
                    "tools": [],
                }
            )
            + "\n"
        )
        lf = LogFile(log_file, 0)
        events = lf.read_new_events()
        assert len(events) == 1
        assert events[0].kind == "init"
        assert lf.model == "opus"

    def test_read_new_events_incremental(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "abc",
                    "model": "opus",
                    "tools": [],
                }
            )
            + "\n"
        )
        lf = LogFile(log_file, 0)
        lf.read_new_events()

        # Append more data
        with log_file.open("a") as f:
            f.write(
                json.dumps({"type": "result", "is_error": False, "cost_usd": 0.5, "duration_s": 10})
                + "\n"
            )

        events = lf.read_new_events()
        assert len(events) == 1
        assert events[0].is_done is True
        assert lf.done is True
        assert lf.cost_usd == 0.5

    def test_read_nonexistent_file(self):
        lf = LogFile(Path("/tmp/nonexistent_file.jsonl"), 0)
        events = lf.read_new_events()
        assert events == []

    def test_rate_limit_events_accumulate_on_logfile(self, tmp_path):
        log_file = tmp_path / "step_AAPL.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "s",
                    "model": "opus",
                    "tools": [],
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "blocked", "rateLimitType": "five_hour"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
                }
            )
            + "\n"
        )
        lf = LogFile(log_file, 0)
        lf.read_new_events()
        assert len(lf.rate_limit_events) == 2
        assert lf.rate_limit_events[0].is_error is True
        assert lf.rate_limit_events[1].is_error is False
        assert lf.rate_limit_events[0].provider == "anthropic"

    def test_flush(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        # Write gemini delta chunks
        lines = [
            json.dumps({"type": "message", "role": "assistant", "delta": True, "content": "hi"})
        ]
        log_file.write_text("\n".join(lines) + "\n")
        lf = LogFile(log_file, 0)
        events = lf.read_new_events()
        # Delta should be buffered
        assert len(events) == 0
        flushed = lf.flush()
        assert len(flushed) == 1
        assert "hi" in flushed[0].summary

    def test_codex_logfile_populates_usage_stats(self, tmp_path):
        """Per-review (3.1): codex turn.completed must populate UsageStats.

        Without this, write-usage / aggregate_usage report zero codex
        contribution even though sum_codex_usage produces correct numbers
        for the LogEvent.summary string.
        """
        log_file = tmp_path / "codex_run.jsonl"
        log_file.write_text(
            json.dumps({"type": "thread.started", "thread_id": "abc-123"})
            + "\n"
            + json.dumps({"type": "turn.started"})
            + "\n"
            + json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 200,
                        "output_tokens": 50,
                    },
                }
            )
            + "\n"
        )
        lf = LogFile(log_file, 0)
        lf.read_new_events()
        assert lf.usage_stats is not None
        assert lf.usage_stats.input_tokens == 800
        assert lf.usage_stats.cache_read_tokens == 200
        assert lf.usage_stats.output_tokens == 50
        assert lf.usage_stats.provider == "openai"
        assert lf.usage_stats.steps == 1
        assert lf.done is True
        assert lf.is_error is False


# ── discover_files ───────────────────────────────────────────────


class TestDiscoverFiles:
    def test_discovers_jsonl_files(self, tmp_path):
        (tmp_path / "a.jsonl").write_text("{}")
        (tmp_path / "b.jsonl").write_text("{}")
        (tmp_path / "c.txt").write_text("not a log")

        known: dict[Path, LogFile] = {}
        counter = [0]
        discover_files([tmp_path], known, counter)

        assert len(known) == 2
        assert counter[0] == 2

    def test_skips_already_known(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text("{}")

        known: dict[Path, LogFile] = {}
        counter = [0]
        discover_files([tmp_path], known, counter)
        discover_files([tmp_path], known, counter)

        assert len(known) == 1
        assert counter[0] == 1

    def test_handles_missing_dir(self):
        known: dict[Path, LogFile] = {}
        counter = [0]
        discover_files([Path("/nonexistent")], known, counter)
        assert len(known) == 0


# ── render_status_line ───────────────────────────────────────────


class TestRenderStatusLine:
    def test_status_line(self, tmp_path):
        files: dict[Path, LogFile] = {}
        p1 = tmp_path / "a.jsonl"
        p1.write_text("{}")
        lf1 = LogFile(p1, 0)
        files[p1] = lf1

        p2 = tmp_path / "b.jsonl"
        p2.write_text("{}")
        lf2 = LogFile(p2, 1)
        lf2.done = True
        files[p2] = lf2

        result = render_status_line(files, use_color=False)
        assert "2 files" in result
        assert "1 active" in result
        assert "1 done" in result


# ── Phase 5: Thinking event tests ───────────────────────────────


class TestPiLogParserThinking:
    def test_message_final_emits_thinking_events(self):
        """PiLogParser should emit kind='thinking' from message_final events."""
        line = json.dumps(
            {
                "type": "message_final",
                "assistantMessageEvent": {
                    "partial": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "Let me analyze the data..."},
                            {"type": "text", "text": "The answer is 42."},
                        ],
                    },
                },
            }
        )
        parser = PiLogParser()
        events = parser.parse_line(line)
        kinds = [e.kind for e in events]
        assert "thinking" in kinds
        assert "text" in kinds
        thinking_evts = [e for e in events if e.kind == "thinking"]
        assert "analyze" in thinking_evts[0].summary

    def test_message_end_emits_thinking_events(self):
        """PiLogParser should emit kind='thinking' from message_end with thinking."""
        line = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Deep thought here."},
                        {"type": "text", "text": "Result text."},
                    ],
                },
            }
        )
        parser = PiLogParser()
        events = parser.parse_line(line)
        kinds = [e.kind for e in events]
        assert "thinking" in kinds
        assert "text" in kinds

    def test_message_end_no_thinking_still_empty(self):
        """message_end without thinking blocks produces no events (text-only)."""
        line = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello."}],
                },
            }
        )
        parser = PiLogParser()
        events = parser.parse_line(line)
        # Now emits text events from message_end
        kinds = [e.kind for e in events]
        assert "thinking" not in kinds
        assert "text" in kinds


class TestClaudeLogParserThinking:
    def test_thinking_block_emits_event(self):
        """ClaudeLogParser should emit kind='thinking' for thinking blocks."""
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "Let me consider..."},
                        {"type": "text", "text": "The result."},
                    ],
                },
            }
        )
        parser = ClaudeLogParser()
        events = parser.parse_line(line)
        kinds = [e.kind for e in events]
        assert "thinking" in kinds
        assert "text" in kinds
        thinking_evts = [e for e in events if e.kind == "thinking"]
        assert "consider" in thinking_evts[0].summary
