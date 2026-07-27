"""Tests for JSONL log compaction."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from metaproc.logutil import compaction as compaction_module
from metaproc.logutil.compaction import (
    COMPACTION_TYPE,
    compact_log,
    compact_logs_path,
    plan_compaction,
    try_compact_log,
)
from metaproc.logutil.parsing import PiLogParser, detect_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "log_compaction"


@pytest.fixture
def pi_log(tmp_path: Path) -> Path:
    src = FIXTURES / "pi_sample.jsonl"
    dst = tmp_path / "pi_sample.jsonl"
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def claude_log(tmp_path: Path) -> Path:
    src = FIXTURES / "claude_sample.jsonl"
    dst = tmp_path / "claude_sample.jsonl"
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def gemini_log(tmp_path: Path) -> Path:
    src = FIXTURES / "gemini_sample.jsonl"
    dst = tmp_path / "gemini_sample.jsonl"
    shutil.copy(src, dst)
    return dst


# ── Pi compaction ────────────────────────────────────────────────


def test_pi_compaction_removes_streaming_events(pi_log: Path) -> None:
    result = compact_log(pi_log)

    assert not result.already_compact
    assert result.adapter == "pi"
    assert result.lines_removed > 0
    assert result.compacted_lines < result.original_lines

    # Verify no message_update, message_start, tool_execution_update,
    # turn_start, turn_end remain.
    compacted_lines = pi_log.read_text().splitlines()
    drop_types = {
        "message_update",
        "message_start",
        "tool_execution_update",
        "turn_start",
        "turn_end",
    }
    for line in compacted_lines:
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                assert event.get("type") not in drop_types, (
                    f"Should have been stripped: {event.get('type')}"
                )
        except (json.JSONDecodeError, ValueError):
            pass


def test_pi_compaction_preserves_important_events(pi_log: Path) -> None:
    # Catalog original events we expect to keep.
    original_keep_types: set[str] = set()
    for line in pi_log.read_text().splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                etype = event.get("type", "")
                if etype not in (
                    "message_update",
                    "message_start",
                    "tool_execution_update",
                    "turn_start",
                    "turn_end",
                ):
                    original_keep_types.add(etype)
        except (json.JSONDecodeError, ValueError):
            pass

    compact_log(pi_log)

    # Verify those event types are still present.
    compacted_types: set[str] = set()
    for line in pi_log.read_text().splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                compacted_types.add(event.get("type", ""))
        except (json.JSONDecodeError, ValueError):
            pass

    # compaction header adds "compaction" type
    compacted_types.discard(COMPACTION_TYPE)
    assert original_keep_types <= compacted_types


def test_pi_compaction_has_header(pi_log: Path) -> None:
    compact_log(pi_log)
    first_line = pi_log.read_text().splitlines()[0]
    header = json.loads(first_line)
    assert header["type"] == COMPACTION_TYPE
    assert header["version"] == 1
    assert header["adapter"] == "pi"
    assert header["original_size"] > 0
    assert header["original_lines"] > 0
    assert "timestamp" in header


# ── Round-trip: parser produces same events ──────────────────────


def test_pi_round_trip_parse_equivalence(pi_log: Path) -> None:
    """PiLogParser should produce the same LogEvents from compacted vs original."""
    original_text = pi_log.read_text()

    # Parse original.
    parser_orig = PiLogParser()
    events_orig = []
    for line in original_text.splitlines():
        events_orig.extend(parser_orig.parse_line(line))
    events_orig.extend(parser_orig.flush())

    # Compact.
    compact_log(pi_log)

    # Parse compacted.
    parser_compact = PiLogParser()
    events_compact = []
    for line in pi_log.read_text().splitlines():
        events_compact.extend(parser_compact.parse_line(line))
    events_compact.extend(parser_compact.flush())

    # The compacted version should have fewer or equal events (message_update
    # events are dropped, and they produced LogEvent(kind="text") entries).
    # Filter to non-text events for strict comparison since message_update
    # produced text events that won't exist post-compaction.
    non_text_orig = [e for e in events_orig if e.kind != "text"]
    # Filter out the compaction header event (kind="system" with "compaction" in summary).
    non_text_compact = [
        e for e in events_compact if e.kind != "text" and COMPACTION_TYPE not in e.summary
    ]

    assert len(non_text_compact) == len(non_text_orig)
    for orig, comp in zip(non_text_orig, non_text_compact, strict=True):
        assert orig.kind == comp.kind


# ── Idempotency ──────────────────────────────────────────────────


def test_idempotency(pi_log: Path) -> None:
    compact_log(pi_log)
    content_after_first = pi_log.read_text()

    result2 = compact_log(pi_log)
    content_after_second = pi_log.read_text()

    assert result2.already_compact
    assert content_after_first == content_after_second


def test_compact_logs_path_filters_by_adapter_and_size(tmp_path: Path) -> None:
    pi_log = tmp_path / "pi.jsonl"
    pi_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "cwd": "/tmp"}),
                *(json.dumps({"type": "message_update", "pad": "x" * 20}) for _ in range(20)),
            ]
        )
        + "\n"
    )
    codex_log = tmp_path / "codex.jsonl"
    codex_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                *(
                    json.dumps({"type": "item.updated", "item": {"text": "partial"}})
                    for _ in range(20)
                ),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ]
        )
        + "\n"
    )
    small_codex = tmp_path / "small-codex.jsonl"
    small_codex.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-2"}) + "\n")

    summary = compact_logs_path(
        tmp_path,
        min_size=200,
        adapters=frozenset({"codex"}),
    )

    assert summary.matched == 3
    assert summary.eligible == 1
    assert summary.compacted == 1
    assert summary.skipped == 2
    assert "compaction" in codex_log.read_text()
    assert "item.updated" not in codex_log.read_text()
    assert "message_update" in pi_log.read_text()
    assert "compaction" not in small_codex.read_text()


def test_plan_compaction_applies_adapter_filter_for_dry_runs(tmp_path: Path) -> None:
    pi_log = tmp_path / "pi.jsonl"
    pi_log.write_text(json.dumps({"type": "session", "version": 3, "cwd": "/tmp"}) + "\n")
    codex_log = tmp_path / "codex.jsonl"
    codex_log.write_text(json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n")

    plan = plan_compaction(tmp_path, adapters=frozenset({"codex"}))

    assert [candidate.path for candidate in plan.candidates] == [codex_log]
    assert plan.eligible == 1
    assert plan.skipped == 1
    assert plan.candidates[0].adapter == "codex"


def test_codex_compaction_preserves_real_error_events(tmp_path: Path) -> None:
    codex_log = tmp_path / "codex.jsonl"
    codex_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps({"type": "item.updated", "item": {"text": "partial"}}),
                json.dumps({"type": "error", "message": "tool auth failed"}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
            ]
        )
        + "\n"
    )

    compact_log(codex_log)

    compacted = codex_log.read_text()
    assert "tool auth failed" in compacted
    assert "item.updated" not in compacted


# ── Claude and Gemini are no-op ──────────────────────────────────


def test_claude_is_noop(claude_log: Path) -> None:
    original_content = claude_log.read_text()
    original_line_count = len(original_content.splitlines())

    result = compact_log(claude_log)

    assert result.adapter == "claude"
    # Only the compaction header is added, no lines removed from original.
    assert result.compacted_lines == original_line_count + 1
    assert result.lines_removed <= 0


def test_gemini_is_noop(gemini_log: Path) -> None:
    original_content = gemini_log.read_text()
    original_line_count = len(original_content.splitlines())

    result = compact_log(gemini_log)

    assert result.adapter == "gemini"
    assert result.compacted_lines == original_line_count + 1
    assert result.lines_removed <= 0


# ── keep_original flag ───────────────────────────────────────────


def test_keep_original_creates_backup(pi_log: Path) -> None:
    original_content = pi_log.read_text()
    compact_log(pi_log, keep_original=True)

    backup = pi_log.with_suffix(".jsonl.bak")
    assert backup.exists()
    assert backup.read_text() == original_content


def test_no_keep_original_removes_backup(pi_log: Path) -> None:
    compact_log(pi_log, keep_original=False)
    backup = pi_log.with_suffix(".jsonl.bak")
    assert not backup.exists()


# ── Adapter detection ────────────────────────────────────────────


def test_detect_pi_adapter() -> None:
    lines = Path(FIXTURES / "pi_sample.jsonl").read_text().splitlines()[:20]
    assert detect_adapter(lines) == "pi"


def test_detect_claude_adapter() -> None:
    lines = Path(FIXTURES / "claude_sample.jsonl").read_text().splitlines()[:20]
    assert detect_adapter(lines) == "claude"


def test_detect_gemini_adapter() -> None:
    lines = Path(FIXTURES / "gemini_sample.jsonl").read_text().splitlines()[:20]
    assert detect_adapter(lines) == "gemini"


# ── Phase 4: thinking preservation ──────────────────────────────


def test_pi_compaction_no_message_final_when_end_has_thinking(pi_log: Path) -> None:
    """Opus-style: message_end has thinking, so no message_final should be emitted."""
    compact_log(pi_log)

    compacted_lines = pi_log.read_text().splitlines()
    final_types = [
        json.loads(line).get("type")
        for line in compacted_lines
        if _is_json_type(line, "message_final")
    ]
    assert final_types == [], "Opus-style logs should not emit message_final"


def test_pi_compaction_emits_message_final_for_deepseek_style(tmp_path: Path) -> None:
    """DeepSeek-style: thinking only in message_update, not in message_end."""
    log_path = tmp_path / "deepseek.jsonl"
    lines = [
        json.dumps({"type": "session", "version": 3, "id": "test", "cwd": "/tmp"}),
        json.dumps({"type": "agent_start"}),
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "message_start", "message": {"role": "assistant", "content": []}}),
        # message_update with thinking
        json.dumps(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "thinking_end",
                    "partial": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "Let me analyze this..."},
                            {"type": "text", "text": "Here is my answer."},
                        ],
                    },
                },
            }
        ),
        # message_end WITHOUT thinking (DeepSeek-style)
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Here is my answer."},
                    ],
                },
            }
        ),
        json.dumps({"type": "turn_end"}),
        json.dumps({"type": "agent_end"}),
    ]
    log_path.write_text("\n".join(lines) + "\n")

    result = compact_log(log_path)

    assert not result.already_compact
    compacted = log_path.read_text().splitlines()

    # Should have exactly one message_final
    finals = [json.loads(line) for line in compacted if _is_json_type(line, "message_final")]
    assert len(finals) == 1

    # The message_final should carry thinking content
    partial = finals[0]["assistantMessageEvent"]["partial"]
    content = partial["content"]
    thinking_blocks = [b for b in content if b.get("type") == "thinking"]
    assert len(thinking_blocks) == 1
    assert "analyze" in thinking_blocks[0]["thinking"]


def test_pi_compaction_no_message_final_without_thinking(tmp_path: Path) -> None:
    """No message_final emitted when message_update has no thinking."""
    log_path = tmp_path / "no_thinking.jsonl"
    lines = [
        json.dumps({"type": "session", "version": 3, "id": "test", "cwd": "/tmp"}),
        json.dumps({"type": "agent_start"}),
        json.dumps(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "partial": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Just text."}],
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Just text."}],
                },
            }
        ),
        json.dumps({"type": "agent_end"}),
    ]
    log_path.write_text("\n".join(lines) + "\n")

    compact_log(log_path)

    compacted = log_path.read_text().splitlines()
    finals = [line for line in compacted if _is_json_type(line, "message_final")]
    assert finals == []


def _is_json_type(line: str, event_type: str) -> bool:
    try:
        return json.loads(line).get("type") == event_type
    except (json.JSONDecodeError, ValueError):
        return False


# ── Log runaway detection ───────────────────────────────────────

from metaproc.logutil.compaction import (  # noqa: E402
    LOG_RUNAWAY_BYTES_PER_TOKEN,
    check_log_runaway,
)

_TEST_LOG_RUNAWAY_SIZE_FLOOR = 1024 * 1024


def _make_pi_log(
    tmp_path: Path, name: str, filler_mb: int, output_tokens_per_msg: int, n_msgs: int
) -> Path:
    """Build a synthetic Pi CLI log with controllable size and token count."""
    log_path = tmp_path / name
    lines: list[str] = [
        json.dumps({"type": "session", "version": 3, "id": "test", "cwd": "/tmp"}),
        json.dumps({"type": "agent_start"}),
    ]
    for _ in range(n_msgs):
        lines.append(
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "x"}],
                        "usage": {"input": 100, "output": output_tokens_per_msg},
                    },
                }
            )
        )
    lines.append(json.dumps({"type": "agent_end"}))

    content = "\n".join(lines) + "\n"
    # Pad with message_update filler to reach desired size.
    current_bytes = len(content.encode())
    target_bytes = filler_mb * 1024 * 1024
    if current_bytes < target_bytes:
        # Each filler line is a message_update with padding.
        padding_needed = target_bytes - current_bytes
        filler_line = json.dumps({"type": "message_update", "pad": "x" * 1000}) + "\n"
        n_filler = padding_needed // len(filler_line.encode()) + 1
        content += filler_line * n_filler

    log_path.write_text(content)
    return log_path


def test_check_log_runaway_below_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files below the size floor return None (too small to judge)."""
    monkeypatch.setattr(compaction_module, "LOG_RUNAWAY_SIZE_FLOOR", 2 * 1024 * 1024)
    log_path = _make_pi_log(
        tmp_path, "small.jsonl", filler_mb=1, output_tokens_per_msg=100, n_msgs=5
    )
    assert check_log_runaway(log_path) is None


def test_check_log_runaway_normal_ratio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A large file with a normal bytes/token ratio is not flagged."""
    monkeypatch.setattr(compaction_module, "LOG_RUNAWAY_SIZE_FLOOR", _TEST_LOG_RUNAWAY_SIZE_FLOOR)
    log_path = _make_pi_log(
        tmp_path, "normal.jsonl", filler_mb=2, output_tokens_per_msg=100, n_msgs=10
    )
    result = check_log_runaway(log_path)
    assert result is not None
    assert not result.is_runaway
    assert result.output_tokens == 1_000


def test_check_log_runaway_detects_anomaly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A large file with extreme bytes/token ratio is flagged."""
    monkeypatch.setattr(compaction_module, "LOG_RUNAWAY_SIZE_FLOOR", _TEST_LOG_RUNAWAY_SIZE_FLOOR)
    log_path = _make_pi_log(
        tmp_path, "runaway.jsonl", filler_mb=2, output_tokens_per_msg=1, n_msgs=1
    )
    result = check_log_runaway(log_path)
    assert result is not None
    assert result.is_runaway
    assert result.output_tokens == 1
    assert result.bytes_per_token > LOG_RUNAWAY_BYTES_PER_TOKEN


def test_check_log_runaway_no_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file with no completed messages returns None."""
    monkeypatch.setattr(compaction_module, "LOG_RUNAWAY_SIZE_FLOOR", _TEST_LOG_RUNAWAY_SIZE_FLOOR)
    log_path = _make_pi_log(
        tmp_path, "no_msgs.jsonl", filler_mb=2, output_tokens_per_msg=0, n_msgs=0
    )
    assert check_log_runaway(log_path) is None


def test_check_log_runaway_missing_file(tmp_path: Path) -> None:
    """A missing file returns None."""
    assert check_log_runaway(tmp_path / "nope.jsonl") is None


def test_try_compact_log_gzips_freshly_written_file(tmp_path: Path) -> None:
    """Regression: post-step hook must gzip a just-written file.

    The default ``exclude_active=True`` in ``gzip_text_path`` silently skipped
    files modified within the last 60s — i.e. every per-attempt log, since the
    hook fires immediately after the agent exits. The fix passes
    ``exclude_active=False`` because the caller knows the writer is dead.
    """
    log_path = tmp_path / "deep-market_AAPL.jsonl"
    # Above the 128 KiB .jsonl threshold so size doesn't disqualify it.
    payload_line = json.dumps({"type": "noise", "text": "x" * 256}) + "\n"
    log_path.write_text(payload_line * 600)
    assert log_path.stat().st_size > 128 * 1024

    try_compact_log(log_path)

    gz_path = log_path.with_suffix(log_path.suffix + ".gz")
    assert gz_path.exists(), "post-step hook must produce a .gz for fresh files"
    assert not log_path.exists(), "original should be unlinked after successful gzip"
