"""The index template must only emit metaproc viz asset tags when the plugin is loaded.

Originally the shell hardcoded ``/plugin-static/metaproc/viz.css`` +
``/plugin-static/metaproc/viz.js`` into the page even when the metaproc
plugin wasn't installed, producing 404 requests on a standalone
metabrowser install. Phase 3j replaced the hardcoded tags with manifest-
driven ``extra_scripts`` / ``extra_styles`` fields the loader reads;
metabrowser core no longer special-cases plugin asset names.

This test still proves the operator-visible behaviour: tags appear iff
the metaproc plugin loads, since the manifest only ships with that
plugin.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

from metabrowser import server


def test_index_emits_viz_tags_when_metaproc_plugin_loaded(tmp_path) -> None:
    server._set_root_dir(tmp_path)  # noqa: SLF001
    response = asyncio.run(server.index(Mock()))
    body = bytes(response.body).decode("utf-8")
    # The metaproc plugin is in our dev install via workspace, so the
    # tags should be present.
    assert "/plugin-static/metaproc/viz.css" in body
    assert "/plugin-static/metaproc/viz.js" in body


def test_index_omits_viz_tags_when_metaproc_plugin_absent(tmp_path) -> None:
    server._set_root_dir(tmp_path)  # noqa: SLF001
    # Pretend no plugins loaded — sanity check that the conditional
    # actually omits the tags.
    with patch.object(server, "_LOADED_PLUGINS", []):
        response = asyncio.run(server.index(Mock()))
    body = bytes(response.body).decode("utf-8")
    assert "/plugin-static/metaproc/viz.css" not in body
    assert "/plugin-static/metaproc/viz.js" not in body
