"""Tests for metaproc.output — OutputManager."""

from __future__ import annotations

import json

from metaproc.output import OutputFormat, OutputManager


def test_text_data_writes_to_stdout(capsys):
    mgr = OutputManager(fmt=OutputFormat.TEXT)
    mgr.data("hello world")
    assert capsys.readouterr().out == "hello world\n"


def test_text_data_renders_dict_as_yaml_not_python_repr(capsys):
    # The default TEXT mode used to fall through to ``str(obj)`` which
    # rendered dicts as Python repr — unparsable by both YAML and
    # JSON. The fix routes non-strings through the YAML dumper so the
    # default output is human- AND machine-readable.
    mgr = OutputManager(fmt=OutputFormat.TEXT)
    mgr.data({"adapter": "claude-code-cli", "label": "alt1", "status": "active"})
    out = capsys.readouterr().out
    # Block-style YAML, no Python single-quoted repr leakage.
    assert "adapter: claude-code-cli" in out
    assert "label: alt1" in out
    assert "status: active" in out
    assert "{'" not in out


def test_text_data_renders_list_as_yaml(capsys):
    mgr = OutputManager(fmt=OutputFormat.TEXT)
    mgr.data([{"label": "alt1"}, {"label": "alt2"}])
    out = capsys.readouterr().out
    assert "label: alt1" in out
    assert "label: alt2" in out
    # YAML list markers, not Python list repr.
    assert "- " in out


def test_yaml_format_dumps_explicit_yaml(capsys):
    mgr = OutputManager(fmt=OutputFormat.YAML)
    mgr.data({"key": "value"})
    out = capsys.readouterr().out
    assert "key: value" in out


def test_json_data_writes_json_to_stdout(capsys):
    mgr = OutputManager(fmt=OutputFormat.JSON)
    mgr.data({"key": "value"})
    out = capsys.readouterr().out
    assert json.loads(out) == {"key": "value"}


def test_progress_suppressed_when_no_progress(capsys):
    mgr = OutputManager(no_progress=True)
    mgr.progress("should not appear")
    assert capsys.readouterr().err == ""


def test_error_writes_to_stderr(capsys):
    mgr = OutputManager()
    mgr.error("something failed")
    assert "something failed" in capsys.readouterr().err


def test_no_color_from_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    mgr = OutputManager()
    assert mgr.no_color is True


def test_ci_env_sets_no_color(monkeypatch):
    monkeypatch.setenv("CI", "true")
    mgr = OutputManager()
    assert mgr.no_color is True
