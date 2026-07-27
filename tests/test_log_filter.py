"""Tests for live JSONL log filtering (_should_drop_line and filter thread)."""

from __future__ import annotations

import io
import json

from metaproc.engine.runtime import _should_drop_line, start_log_filter_thread

# ── _should_drop_line unit tests ──────────────────────────────────


class TestShouldDropLine:
    """Tests for the _should_drop_line predicate."""

    def test_drops_message_update(self) -> None:
        line = json.dumps({"type": "message_update", "partial": {"content": "hello"}})
        assert _should_drop_line(line) is True

    def test_drops_tool_execution_update(self) -> None:
        line = json.dumps({"type": "tool_execution_update", "toolCallId": "123"})
        assert _should_drop_line(line) is True

    def test_keeps_message_end(self) -> None:
        line = json.dumps({"type": "message_end", "message": {"role": "assistant"}})
        assert _should_drop_line(line) is False

    def test_keeps_tool_execution_start(self) -> None:
        line = json.dumps({"type": "tool_execution_start", "toolName": "Read"})
        assert _should_drop_line(line) is False

    def test_keeps_tool_execution_end(self) -> None:
        line = json.dumps({"type": "tool_execution_end", "toolName": "Read"})
        assert _should_drop_line(line) is False

    def test_keeps_agent_start(self) -> None:
        line = json.dumps({"type": "agent_start"})
        assert _should_drop_line(line) is False

    def test_keeps_agent_end(self) -> None:
        line = json.dumps({"type": "agent_end"})
        assert _should_drop_line(line) is False

    def test_keeps_line_without_type(self) -> None:
        line = json.dumps({"data": "no type field"})
        assert _should_drop_line(line) is False

    def test_keeps_non_dict_json(self) -> None:
        line = json.dumps([1, 2, 3])
        assert _should_drop_line(line) is False

    def test_keeps_malformed_json(self) -> None:
        assert _should_drop_line("{broken json") is False

    def test_keeps_empty_line(self) -> None:
        assert _should_drop_line("") is False

    def test_keeps_non_json_text(self) -> None:
        assert _should_drop_line("some stderr output") is False

    def test_false_positive_guard_message_update_in_content(self) -> None:
        """A message_end whose content field mentions 'message_update' should be kept."""
        line = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": 'The event type is "message_update" and it streams.',
                        }
                    ]
                },
            }
        )
        assert _should_drop_line(line) is False

    def test_false_positive_guard_tool_execution_update_in_content(self) -> None:
        """A message_end mentioning 'tool_execution_update' in content should be kept."""
        line = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "content": [
                        {"type": "text", "text": "tool_execution_update events are cumulative."}
                    ]
                },
            }
        )
        assert _should_drop_line(line) is False

    def test_handles_compact_json(self) -> None:
        """No space after colon: {"type":"message_update"}"""
        line = '{"type":"message_update","partial":{}}'
        assert _should_drop_line(line) is True

    def test_handles_spaced_json(self) -> None:
        """Space after colon: {"type": "message_update"}"""
        line = '{"type": "message_update", "partial": {}}'
        assert _should_drop_line(line) is True


# ── start_log_filter_thread integration tests ───────────────────


class TestLogFilterThread:
    """Integration tests for the filter thread reading from a pipe."""

    def _run_filter(self, input_lines: list[str]) -> list[str]:
        """Feed lines through the filter thread and return the output."""
        raw = "".join(line + "\n" for line in input_lines).encode("utf-8")
        pipe = io.BytesIO(raw)
        output = io.StringIO()
        # Prevent the thread from closing our StringIO.
        original_close = output.close
        output.close = lambda: None  # type: ignore[assignment]

        thread = start_log_filter_thread(pipe, output)
        thread.join(timeout=5.0)

        output.close = original_close  # type: ignore[assignment]
        output.seek(0)
        return [line.rstrip("\n") for line in output.readlines()]

    def test_filters_message_update_keeps_rest(self) -> None:
        lines = [
            json.dumps({"type": "agent_start"}),
            json.dumps({"type": "message_update", "partial": {"content": "a"}}),
            json.dumps({"type": "message_update", "partial": {"content": "ab"}}),
            json.dumps({"type": "message_update", "partial": {"content": "abc"}}),
            json.dumps({"type": "message_end", "message": {"content": "abc"}}),
            json.dumps({"type": "tool_execution_start", "toolName": "Write"}),
            json.dumps({"type": "tool_execution_update", "result": "partial"}),
            json.dumps({"type": "tool_execution_end", "toolName": "Write"}),
            json.dumps({"type": "agent_end"}),
        ]
        result = self._run_filter(lines)
        types = [json.loads(line)["type"] for line in result]
        assert types == [
            "agent_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "agent_end",
        ]

    def test_preserves_non_json_lines(self) -> None:
        lines = [
            "some stderr output",
            json.dumps({"type": "message_update", "partial": {}}),
            "another stderr line",
        ]
        result = self._run_filter(lines)
        assert result == ["some stderr output", "another stderr line"]

    def test_empty_input(self) -> None:
        result = self._run_filter([])
        assert result == []

    def test_all_updates_filtered(self) -> None:
        lines = [
            json.dumps({"type": "message_update", "partial": {"content": "x"}}),
            json.dumps({"type": "tool_execution_update", "result": "y"}),
        ]
        result = self._run_filter(lines)
        assert result == []
