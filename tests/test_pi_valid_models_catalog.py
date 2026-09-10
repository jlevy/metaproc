"""Regression: PI_VALID_MODELS must cover every model in pi-models.default.json.

Every packaged provider model must pass adapter validation. Command construction
rejects unknown explicit selections instead of substituting the default.
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
        f"pi-models.default.json declares model IDs that PI_VALID_MODELS rejects: {sorted(missing)}"
    )
