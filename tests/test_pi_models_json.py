"""Tests for build_pi_models_json fallback + rewrite behavior."""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from metaproc.cloud.gcp.batch_backend import (
    _merge_pi_models_json,
    _rewrite_pi_models_json,
    build_pi_models_json,
)
from metaproc.config.model_catalog import MODEL_CATALOG


def test_rewrite_swaps_gcloud_apikey_helper() -> None:
    src = json.dumps(
        {
            "providers": {
                "vertex-maas": {
                    "baseUrl": "https://aiplatform.googleapis.com/v1/projects/other/locations/global/endpoints/openapi",
                    "apiKey": "!gcloud auth print-access-token",
                }
            }
        }
    )
    out = json.loads(_rewrite_pi_models_json(src, "exampletool"))
    assert "METAPROC_PI_API_KEY" in out["providers"]["vertex-maas"]["apiKey"]
    assert "gcp-access-token.sh" in out["providers"]["vertex-maas"]["apiKey"]


def test_rewrite_retargets_project_in_vertex_base_url() -> None:
    src = json.dumps(
        {
            "providers": {
                "vertex-maas": {
                    "baseUrl": "https://aiplatform.googleapis.com/v1/projects/other/locations/global/endpoints/openapi",
                    "apiKey": "!gcp-access-token.sh",
                }
            }
        }
    )
    out = json.loads(_rewrite_pi_models_json(src, "exampletool"))
    assert (
        out["providers"]["vertex-maas"]["baseUrl"]
        == "https://aiplatform.googleapis.com/v1/projects/exampletool/locations/global/endpoints/openapi"
    )


def test_rewrite_turns_bare_api_key_env_name_into_reference(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bare `*_API_KEY` name is a literal key to Pi, so it becomes `$NAME`."""
    src = json.dumps(
        {
            "providers": {
                "openai": {"apiKey": "OPENAI_API_KEY"},
                "deepseek": {"apiKey": "DEEPSEEK_API_KEY"},
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="metaproc.cloud.gcp.batch_backend"):
        out = json.loads(_rewrite_pi_models_json(src, "exampletool"))
    assert out["providers"]["openai"]["apiKey"] == "$OPENAI_API_KEY"
    assert out["providers"]["deepseek"]["apiKey"] == "$DEEPSEEK_API_KEY"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'openai'" in w and "$OPENAI_API_KEY" in w for w in warnings)
    assert any("'deepseek'" in w for w in warnings)


def test_rewrite_leaves_cloud_credential_references_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    providers = {
        "ref": {
            "apiKey": "$OPENAI_API_KEY",
            "headers": {"Authorization": "Bearer ${AUTH_TOKEN}", "X-API-Key": "$HEADER_KEY"},
        },
        "cmd": {"apiKey": "!gcp-access-token.sh"},
        "creds": {"api": "google-vertex", "apiKey": "gcp-vertex-credentials"},
    }
    with caplog.at_level(logging.WARNING, logger="metaproc.cloud.gcp.batch_backend"):
        out = json.loads(
            _rewrite_pi_models_json(json.dumps({"providers": providers}), "exampletool")
        )
    assert out["providers"] == providers
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    "fields",
    [
        {"apiKey": "example-private-value"},
        {"apiKey": "!echo example-private-value"},
        {"headers": {"Authorization": "Bearer example-private-value"}},
        {"headers": {"CF-Access-Client-Secret": "example-private-value"}},
        {"models": [{"id": "demo", "headers": {"X-API-Key": "example-private-value"}}]},
        {"modelOverrides": {"demo": {"headers": {"x-portkey-api-key": "example-private-value"}}}},
    ],
)
def test_build_refuses_operator_credentials_in_batch_metadata(
    fields: dict[str, Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pi_dir = tmp_path / ".pi" / "agent"
    pi_dir.mkdir(parents=True)
    (pi_dir / "models.json").write_text(json.dumps({"providers": {"operator": fields}}))

    with (
        patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False),
        pytest.raises(ValueError, match="Pi provider 'operator'.*Secret Manager") as exc,
    ):
        build_pi_models_json("exampletool")

    assert "example-private-value" not in str(exc.value)
    assert "example-private-value" not in caplog.text


def test_build_rewrites_bare_api_key_from_operator_overlay(tmp_path: Path) -> None:
    """A stale operator overlay must not reintroduce the literal-key 401."""
    operator_models = {"providers": {"openai": {"apiKey": "OPENAI_API_KEY"}}}
    pi_dir = tmp_path / ".pi" / "agent"
    pi_dir.mkdir(parents=True)
    (pi_dir / "models.json").write_text(json.dumps(operator_models))

    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        out = json.loads(build_pi_models_json("exampletool"))

    assert out["providers"]["openai"]["apiKey"] == "$OPENAI_API_KEY"


def test_build_merges_operator_file_into_packaged(tmp_path: Path) -> None:
    """Operator-added models augment the packaged vertex-maas block; rewrite still runs."""
    operator_models = {
        "providers": {
            "vertex-maas": {
                "baseUrl": "https://aiplatform.googleapis.com/v1/projects/staging/locations/global/endpoints/openapi",
                "apiKey": "!gcloud auth print-access-token",
                "models": [{"id": "operator/custom-model"}],
            }
        }
    }
    pi_dir = tmp_path / ".pi" / "agent"
    pi_dir.mkdir(parents=True)
    (pi_dir / "models.json").write_text(json.dumps(operator_models))

    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        out = json.loads(build_pi_models_json("exampletool"))

    model_ids = {m["id"] for m in out["providers"]["vertex-maas"]["models"]}
    assert "operator/custom-model" in model_ids
    # Packaged models survive the overlay — the whole point of merge semantics.
    assert "zai-org/glm-5.2-maas" in model_ids
    # Project rewrite and apiKey swap both fire on the merged result.
    assert "projects/exampletool/" in out["providers"]["vertex-maas"]["baseUrl"]
    assert "METAPROC_PI_API_KEY" in out["providers"]["vertex-maas"]["apiKey"]
    assert "gcp-access-token.sh" in out["providers"]["vertex-maas"]["apiKey"]


def test_build_falls_back_to_packaged_default(tmp_path: Path) -> None:
    # HOME is a clean tmp dir with no ~/.pi/agent/models.json.
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        raw = build_pi_models_json("exampletool")

    assert raw, "fallback should return a non-empty models.json"
    out = json.loads(raw)
    providers = out["providers"]
    assert "vertex-maas" in providers
    vertex = providers["vertex-maas"]
    assert vertex["apiKey"] == (
        '!sh -c \'if [ -n "$METAPROC_PI_API_KEY" ]; then '
        'printf "%s" "$METAPROC_PI_API_KEY"; else exec gcp-access-token.sh; fi\''
    )
    assert (
        vertex["baseUrl"]
        == "https://aiplatform.googleapis.com/v1/projects/exampletool/locations/global/endpoints/openapi"
    )
    model_ids = {m["id"] for m in vertex["models"]}
    assert model_ids == {
        "zai-org/glm-5.2-maas",
        "deepseek-ai/deepseek-v4.1-flash-maas",
        "deepseek-ai/deepseek-v4.1-flash-1017-maas",
    }
    models = {m["id"]: m for m in vertex["models"]}
    glm = models["zai-org/glm-5.2-maas"]
    assert (glm["contextWindow"], glm["maxTokens"], glm["reasoning"]) == (1000000, 64000, True)
    for model_id in (
        "deepseek-ai/deepseek-v4.1-flash-maas",
        "deepseek-ai/deepseek-v4.1-flash-1017-maas",
    ):
        flash = models[model_id]
        assert (flash["contextWindow"], flash["maxTokens"]) == (1048576, 1048576)
        assert flash["reasoning"] is True


def test_packaged_default_includes_google_vertex_provider(tmp_path: Path) -> None:
    """google-vertex (Gemini via Vertex native API) parses cleanly under the rewrite pipeline.

    Migrated 2026-04-18 from `openai-completions` (Vertex OpenAI-compat shim,
    which drops Gemini 3 thought_signature) to `google-vertex` (pi-mono's
    native @google/genai path, which handles thought_signature). See
    metaproc/docs/runbooks/adapter-compatibility.runbook.md.
    """
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        raw = build_pi_models_json("exampletool")

    out = json.loads(raw)
    providers = out["providers"]
    assert "google-vertex" in providers, "google-vertex provider required for Gemini lanes"

    gv = providers["google-vertex"]
    # Native Vertex API — ADC via metadata server covers auth in the container.
    # pi-mono's hasConfiguredAuth gate requires apiKey != undefined; the
    # `gcp-vertex-credentials` sentinel passes that gate and google-vertex.ts's
    # resolveApiKey recognizes it as "use ADC" (see
    # attic/pi-mono/packages/ai/src/providers/google-vertex.ts:372).
    assert gv["api"] == "google-vertex"
    assert gv["apiKey"] == "gcp-vertex-credentials"
    # pi-mono's google-vertex provider substitutes {location} itself; baseUrl
    # stays templated (project is not part of the URL for the native SDK).
    assert gv["baseUrl"] == "https://{location}-aiplatform.googleapis.com"

    # Native Vertex uses unprefixed publisher model names. Pi 1.5.2 ships the
    # other accepted Gemini models, including gemini-3.1-pro-preview-customtools
    # with its 1,048,576-token context, so only Gemini 4 Carbon and Hydrogen
    # are defined here.
    models = {m["id"]: m for m in gv["models"]}
    assert set(models) == {"gemini-4-carbon", "gemini-4-hydrogen"}
    for model in models.values():
        assert model["contextWindow"] == 1048576
        assert model["maxTokens"] == 65536
        assert model["reasoning"] is True
        # Pi's Google thinking map covers low through high; minimal and off are
        # rejected by Gemini 4 and must clamp rather than reach the API.
        levels = model["thinkingLevelMap"]
        assert levels["minimal"] is None
        assert levels["off"] is None
        assert {levels[level] for level in ("low", "medium", "high")} == {
            "low",
            "medium",
            "high",
        }
        assert model["cost"]["input"] > 0


def test_packaged_default_includes_openai_provider(tmp_path: Path) -> None:
    """OpenAI models are Pi built-ins; only deliberate differences and new IDs are listed."""
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        raw = build_pi_models_json("exampletool")

    out = json.loads(raw)
    oa = out["providers"]["openai"]
    assert oa["baseUrl"] == "https://api.openai.com/v1"
    # Built-in openai models use Pi's env lookup; a literal apiKey would replace it.
    assert "apiKey" not in oa

    overrides = oa["modelOverrides"]
    for model_id in (
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ):
        assert overrides[model_id] == {"contextWindow": 1050000}
    assert overrides["gpt-5.5"] == {"contextWindow": 1000000}

    models = {m["id"]: m for m in oa["models"]}
    # GPT-6.2 postdates Pi 1.5.2's built-in catalog.
    assert set(models) == {"gpt-6.2-astra", "gpt-6.2-sol"}
    for model in models.values():
        assert model["api"] == "openai-responses"
        assert model["contextWindow"] == 1050000
        assert model["maxTokens"] == 128000
        # The GPT-6.2 API rejects reasoning effort `none`.
        assert model["thinkingLevelMap"]["off"] is None
        assert model["cost"]["input"] > 0


def test_packaged_default_turns_off_anthropic_message_threads(tmp_path: Path) -> None:
    """Pi 1.5.1 enabled Anthropic message threads by default; the packaged file keeps them off."""
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        out = json.loads(build_pi_models_json("exampletool"))

    anthropic = out["providers"]["anthropic"]
    assert anthropic["compat"] == {"supportsMessageThreads": False}
    assert "models" not in anthropic


def test_packaged_default_moonshot_models(tmp_path: Path) -> None:
    """The moonshot route lists current Kimi models plus the closing kimi-k2.6."""
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        out = json.loads(build_pi_models_json("exampletool"))

    moonshot = out["providers"]["moonshot"]
    assert moonshot["baseUrl"] == "https://api.moonshot.ai/v1"
    models = {m["id"]: m for m in moonshot["models"]}
    assert set(models) == {"kimi-k3.1-pro", "kimi-k3", "kimi-k2.6"}
    for model in models.values():
        # Copied from Pi's built-in moonshotai entries, including the wire format.
        assert model["compat"]["thinkingFormat"] in {"openai", "deepseek"}
        assert model["cost"]["input"] > 0
    assert "deepseek" not in out["providers"], "DeepSeek models are Pi built-ins"


def _packaged_doc() -> dict[str, Any]:
    raw = resources.files("metaproc.data").joinpath("pi-models.default.json").read_text()
    return cast("dict[str, Any]", json.loads(raw))


def test_packaged_api_keys_are_never_literal_env_names() -> None:
    """Since Pi 0.77 a bare apiKey string is a literal key that outranks the environment.

    `"OPENAI_API_KEY"` would send that text as the bearer token, so every apiKey must
    be a `$NAME` reference, a `!command`, or the Vertex ADC sentinel.
    """
    for name, provider in _packaged_doc()["providers"].items():
        api_key = provider.get("apiKey")
        if api_key is None:
            continue
        assert isinstance(api_key, str)
        assert api_key.startswith(("$", "!")) or api_key == "gcp-vertex-credentials", (
            f"provider {name!r} has a literal apiKey {api_key!r}"
        )


def test_packaged_models_do_not_duplicate_accepted_native_models() -> None:
    """A models[] entry with a built-in ID replaces Pi's whole definition.

    That zeroes its cost and drops its thinking map and compat flags, so packaged
    entries must not repeat the built-in IDs Metaproc accepts; deliberate
    differences belong in modelOverrides.
    """
    native = MODEL_CATALOG.native_models["pi-cli"]
    for name, provider in _packaged_doc()["providers"].items():
        if name == "moonshot":
            # A separate provider ID: its entries do not replace Pi's moonshotai models.
            continue
        for model in provider.get("models", []):
            assert model["id"] not in native, (name, model["id"])
        for model_id in provider.get("modelOverrides", {}):
            assert model_id in native, (name, model_id)


# Field names from Pi 1.5.2's schemas/models.schema.json (also published at
# https://pi.dev/schemas/models.schema.json). Keys starting with `_` are notes.
_PI_PROVIDER_KEYS = frozenset(
    {
        "api",
        "apiKey",
        "authHeader",
        "baseUrl",
        "compat",
        "headers",
        "modelOverrides",
        "models",
        "name",
        "oauth",
    }
)
_PI_MODEL_KEYS = frozenset(
    {
        "api",
        "baseUrl",
        "compat",
        "contextWindow",
        "cost",
        "headers",
        "id",
        "input",
        "inputLimits",
        "maxTokens",
        "name",
        "promptCache",
        "reasoning",
        "samplingParams",
        "samplingParamsByThinkingLevel",
        "thinkingLevelMap",
    }
)
_PI_OVERRIDE_KEYS = _PI_MODEL_KEYS - {"api", "baseUrl", "id"}


def test_packaged_default_uses_only_pi_schema_fields() -> None:
    for name, provider in _packaged_doc()["providers"].items():
        assert set(provider) - _PI_PROVIDER_KEYS <= {"_note"}, name
        for model in provider.get("models", []):
            assert set(model) <= _PI_MODEL_KEYS, (name, model["id"])
            for key in ("input", "output", "cacheRead", "cacheWrite"):
                if "cost" in model:
                    assert key in model["cost"], (name, model["id"], key)
        for model_id, override in provider.get("modelOverrides", {}).items():
            assert set(override) <= _PI_OVERRIDE_KEYS, (name, model_id)


def test_merge_only_local_adds_new_provider() -> None:
    """Local-only provider is admitted; packaged providers remain intact."""
    default_doc = {
        "providers": {
            "vertex-maas": {"apiKey": "!gcp-access-token.sh", "models": [{"id": "pkg-a"}]},
        }
    }
    local_doc = {
        "providers": {
            "my-custom": {"apiKey": "!env:CUSTOM_KEY", "models": [{"id": "local-only-1"}]},
        }
    }
    merged = _merge_pi_models_json(default_doc, local_doc)

    assert "vertex-maas" in merged["providers"]
    assert "my-custom" in merged["providers"]
    assert [m["id"] for m in merged["providers"]["vertex-maas"]["models"]] == ["pkg-a"]
    assert [m["id"] for m in merged["providers"]["my-custom"]["models"]] == ["local-only-1"]


def test_merge_local_override_wins_per_field() -> None:
    """Local provider fields override defaults; local models by id override; packaged-only ids survive."""
    default_doc = {
        "providers": {
            "openai": {
                "baseUrl": "https://api.openai.com/v1",
                "api": "openai-completions",
                "models": [
                    {"id": "gpt-5.4", "name": "GPT-5.4", "contextWindow": 400000},
                    {"id": "gpt-5.2", "name": "GPT-5.2", "contextWindow": 400000},
                ],
            }
        }
    }
    local_doc = {
        "providers": {
            "openai": {
                "baseUrl": "https://corp-proxy.example/openai",
                "models": [
                    {"id": "gpt-5.4", "name": "GPT-5.4 (corp tuned)", "contextWindow": 128000},
                    {"id": "gpt-5.1", "name": "GPT-5.1", "contextWindow": 200000},
                ],
            }
        }
    }
    merged = _merge_pi_models_json(default_doc, local_doc)

    provider = merged["providers"]["openai"]
    # Local field wins on conflict.
    assert provider["baseUrl"] == "https://corp-proxy.example/openai"
    # Field absent in local survives from default.
    assert provider["api"] == "openai-completions"

    models_by_id = {m["id"]: m for m in provider["models"]}
    # Local model overrides packaged entry with same id.
    assert models_by_id["gpt-5.4"]["name"] == "GPT-5.4 (corp tuned)"
    assert models_by_id["gpt-5.4"]["contextWindow"] == 128000
    # Packaged-only id survives.
    assert "gpt-5.2" in models_by_id
    # Local-only id is admitted.
    assert "gpt-5.1" in models_by_id
