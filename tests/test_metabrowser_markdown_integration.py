"""Markdown integration contracts for the metaproc MetaBrowser plugin."""

from __future__ import annotations

from metaproc.metabrowser_plugin import plugin_dir


def test_process_views_reuse_builtin_markdown_renderers() -> None:
    """Process rendered/source views reuse core Markdown implementations."""
    plugin_index = (plugin_dir() / "index.js").read_text(encoding="utf-8")
    assert 'registerView("process-spec", "rendered"' in plugin_index
    assert 'registerView("process-spec", "source"' in plugin_index
    assert "mb.builtins.markdown" in plugin_index
