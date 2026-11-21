"""Regression: PI_VALID_MODELS must cover every model in pi-models.default.json.

Every packaged provider model must pass adapter validation. Command construction
rejects unknown explicit selections instead of substituting the default.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from metaproc.config.model_catalog import known_model_ids, resolve_model
from metaproc.settings import PI_VALID_MODELS


def test_pi_valid_models_covers_packaged_catalog() -> None:
    raw = resources.files("metaproc.data").joinpath("pi-models.default.json").read_text()
    catalog = json.loads(raw)
    catalog_ids: set[str] = set()
    for cfg in catalog["providers"].values():
        # A provider block may carry only compat or modelOverrides for Pi's
        # built-in models (for example `anthropic`), with no models of its own.
        for model in cfg.get("models", []):
            catalog_ids.add(model["id"])
    missing = catalog_ids - PI_VALID_MODELS
    assert not missing, (
        f"pi-models.default.json declares model IDs that PI_VALID_MODELS rejects: {sorted(missing)}"
    )


@pytest.mark.parametrize(
    "model",
    [
        "glm-5.2-maas",
        "deepseek-v4.1-flash-maas",
        "deepseek-v4.1-flash-1017-maas",
        "deepseek-flash",
        "deepseek-v4-pro",
        "kimi-k3.1-pro",
        "kimi-k3",
        "gpt-6.2-sol",
        "gemini-3.6-flash",
        "fable",
    ],
)
def test_pi_accepts_current_models(model: str) -> None:
    assert model in known_model_ids("pi-cli")
    assert resolve_model("pi-cli", model) == model


@pytest.mark.parametrize(
    "model",
    [
        # Retired Vertex MaaS IDs, in both forms Pi's lookup would take.
        "zai-org/glm-4.7-maas",
        "glm-4.7-maas",
        "moonshotai/kimi-k2-thinking-maas",
        "kimi-k2-thinking-maas",
        "deepseek-ai/deepseek-v3.2-maas",
        "deepseek-v3.2-maas",
        "qwen/qwen3-235b-a22b-instruct-2507-maas",
        "qwen3-coder-480b-a35b-instruct-maas",
        # DeepSeek's retired Flash alias, and superseded OpenAI and Gemini IDs.
        "deepseek-v4-flash",
        "gpt-5.4-nano",
        "gemini-3.7-flash",
        # Pi has no gpt-5.6 model; its fuzzy lookup would pick GPT-5.6 Terra, not Sol.
        "gpt-5.6",
    ],
)
def test_pi_rejects_retired_models_without_substitution(model: str) -> None:
    assert model not in known_model_ids("pi-cli")
    with pytest.raises(ValueError, match="An explicit model is never replaced"):
        resolve_model("pi-cli", model)
