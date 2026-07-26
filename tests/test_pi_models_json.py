"""Tests for build_pi_models_json fallback + rewrite behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from metaproc.cloud.gcp.batch_backend import (
    _merge_pi_models_json,
    _rewrite_pi_models_json,
    build_pi_models_json,
)


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
    assert out["providers"]["vertex-maas"]["apiKey"] == "!gcp-access-token.sh"


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
    assert "zai-org/glm-5-maas" in model_ids
    # Project rewrite and apiKey swap both fire on the merged result.
    assert "projects/exampletool/" in out["providers"]["vertex-maas"]["baseUrl"]
    assert out["providers"]["vertex-maas"]["apiKey"] == "!gcp-access-token.sh"


def test_build_falls_back_to_packaged_default(tmp_path: Path) -> None:
    # HOME is a clean tmp dir with no ~/.pi/agent/models.json.
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        raw = build_pi_models_json("exampletool")

    assert raw, "fallback should return a non-empty models.json"
    out = json.loads(raw)
    providers = out["providers"]
    assert "vertex-maas" in providers
    vertex = providers["vertex-maas"]
    assert vertex["apiKey"] == "!gcp-access-token.sh"
    assert (
        vertex["baseUrl"]
        == "https://aiplatform.googleapis.com/v1/projects/exampletool/locations/global/endpoints/openapi"
    )
    model_ids = {m["id"] for m in vertex["models"]}
    assert {
        "zai-org/glm-5-maas",
        "zai-org/glm-4.7-maas",
        "deepseek-ai/deepseek-v3.2-maas",
    } <= model_ids


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

    model_ids = {m["id"] for m in gv["models"]}
    # Naked IDs — pi-mono's built-in google-vertex catalog uses unprefixed
    # model names. Covers gemini-3.5-flash (GA flagship Flash), the
    # 3.1 preview variants, gemini-3.1-flash-lite (GA), and gemini-3-flash-preview.
    assert "gemini-3.5-flash" in model_ids
    assert "gemini-3.1-flash-lite" in model_ids
    assert "gemini-3-flash-preview" in model_ids
    assert "gemini-3.1-pro-preview" in model_ids
    assert "gemini-3.1-pro-preview-customtools" in model_ids


def test_packaged_default_includes_openai_provider(tmp_path: Path) -> None:
    """openai provider (gpt-5.x + o-series) is available to cloud dispatch without an operator file."""
    with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
        raw = build_pi_models_json("exampletool")

    out = json.loads(raw)
    providers = out["providers"]
    assert "openai" in providers, "openai provider required for gpt-5.x lanes"

    oa = providers["openai"]
    assert oa["api"] == "openai-completions"
    assert oa["baseUrl"] == "https://api.openai.com/v1"

    model_ids = {m["id"] for m in oa["models"]}
    # gpt-5.5-pro is deliberately absent — it requires /v1/responses and
    # pi-cli's openai-completions provider cannot dispatch it. It lives
    # in CODEX_VALID_MODELS instead. See bead internal-reference.
    assert {"gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"} <= model_ids
    assert "gpt-5.5-pro" not in model_ids


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
