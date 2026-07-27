"""Regression: PI_VALID_MODELS must cover every model in pi-models.default.json.

If the adapter's allowlist lacks a model ID that process specs reference via
pi-models.default.json, `_build_pi_flags` silently falls back to
PI_DEFAULT_MODEL ("sonnet"), producing 100 %% invalid_output on cloud runs.
That bug cost 4 Gemini dispatches on 2026-04-17. This test catches it.
"""

from __future__ import annotations

import json
from importlib import resources

from metaproc.settings import PI_VALID_MODELS


def test_pi_valid_models_covers_packaged_catalog() -> None:
    raw = resources.files("metaproc.data").joinpath("pi-models.default.json").read_text()
    catalog = json.loads(raw)
    catalog_ids: set[str] = set()
    for cfg in catalog["providers"].values():
        for model in cfg["models"]:
            catalog_ids.add(model["id"])
    missing = catalog_ids - PI_VALID_MODELS
    assert not missing, (
        "pi-models.default.json declares model IDs that PI_VALID_MODELS "
        f"rejects (adapter would silently fall back to PI_DEFAULT_MODEL): {sorted(missing)}"
    )
