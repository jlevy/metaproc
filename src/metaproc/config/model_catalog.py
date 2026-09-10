"""Reviewed model names, defaults, and source evidence for adapter validation.

Acceptance means Metaproc preserves an identifier, not that every account, client,
or API can serve it. Pi deployment IDs come from its packaged provider catalog;
native CLI names and historical exceptions are maintained here.
"""

from __future__ import annotations

from datetime import date
from functools import cache
from importlib import resources

from pydantic import BaseModel, ConfigDict


class ModelCatalog(BaseModel):
    """Dated review evidence; model availability remains provider-authoritative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewed_on: date
    review_interval_days: int
    sources: dict[str, tuple[str, ...]]
    defaults: dict[str, str]
    native_models: dict[str, frozenset[str]]
    lifecycle_notes: dict[str, str]


MODEL_CATALOG = ModelCatalog(
    reviewed_on=date(2026, 9, 10),
    review_interval_days=30,
    sources={
        "openai": (
            "https://learn.chatgpt.com/docs/models",
            "https://developers.openai.com/api/docs/models",
            "https://learn.chatgpt.com/docs/config-file/config-reference",
            "https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/protocol/src/openai_models.rs",
        ),
        "anthropic": (
            "https://platform.claude.com/docs/en/models/overview",
            "https://platform.claude.com/docs/en/about-claude/model-deprecations",
            "https://code.claude.com/docs/en/model-config",
        ),
        "google": (
            "https://ai.google.dev/gemini-api/docs/models",
            "https://ai.google.dev/gemini-api/docs/deprecations",
            "https://ai.google.dev/gemini-api/docs/thinking",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-6-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-pro",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-flash",
            "https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/config/models.ts",
        ),
        "pi": (
            "https://github.com/earendil-works/pi-mono/tree/v0.84.2/packages/ai/src/api",
            "https://github.com/earendil-works/pi/releases/download/v0.84.2/pi-0.84.2-source.tar.gz",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models",
            "https://api-docs.deepseek.com/quick_start/pricing/",
            "https://platform.moonshot.ai/docs/guide/prompt-best-practice",
        ),
    },
    # Defaults are policy choices, not aliases for the newest release. A catalog
    # refresh must not silently migrate existing workloads to another default.
    defaults={
        "claude-code-cli": "opus",
        "gemini-cli": "gemini-3.1-pro-preview-customtools",
        "codex-cli": "gpt-5.5",
        "pi-cli": "sonnet",
    },
    native_models={
        "claude-code-cli": frozenset(
            {
                "claude-fable-5-1",
                "claude-fable-5",
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-opus-4-8",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "fable",
                "opus",
                "sonnet",
                "haiku",
            }
        ),
        "codex-cli": frozenset(
            {
                "gpt-6-astra",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.6",
                "gpt-5.3-codex-spark",
                "gpt-5.5",
                "gpt-5.5-pro",
                "gpt-5.4",
                "gpt-5.4-mini",
            }
        ),
        "gemini-cli": frozenset(
            {
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.1-pro-preview",
                "gemini-3.1-pro-preview-customtools",
                "gemini-3-flash-preview",
                "gemini-3-pro-preview",
                "gemini-3.1-flash-lite-preview",
                "auto",
                "auto-gemini-3",
                "pro",
                "flash",
                "flash-lite",
            }
        ),
        # Native Pi selections retained from released versions. Custom provider
        # entries are derived below; adding one never needs a second allowlist edit.
        "pi-cli": frozenset(
            {
                "claude-fable-5",
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-opus-4-8",
                "sonnet",
                "opus",
                "haiku",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
            }
        ),
    },
    lifecycle_notes={
        "claude-fable-5-1": "Current Anthropic ID. Claude Code requires at least 2.1.257, newer than this repository's 2.1.234 pin. Absent from the pinned Pi 0.84.2 native catalog; not accepted by the Pi adapter.",
        "gpt-5.6": "Documented Sol alias; retain the requested alias in argv.",
        "gpt-5.3-codex-spark": "Codex research preview; account and plan dependent.",
        "gpt-5.4": "Retained ID. Codex ChatGPT sign-in retirement: 2026-08-31; API-key use differs.",
        "gpt-5.4-mini": "Retained ID. Codex ChatGPT sign-in retirement: 2026-08-31; API-key use differs.",
        "gpt-5.5-pro": "Retained Codex ID; API existence does not establish Codex account access.",
        "gemini-3-pro-preview": "Retired 2026-03-09; retained for historical configuration compatibility. Replacement: gemini-3.1-pro-preview.",
        "gemini-3.1-flash-lite-preview": "Retired 2026-05-25; retained for historical configuration compatibility. Replacement: gemini-3.1-flash-lite.",
        "gemini-3.1-flash-lite": "Published shutdown: 2027-05-07. Replacement: gemini-3.5-flash-lite.",
        "gemini-3.1-pro-preview-customtools": "Gemini CLI-specific preview route retained; not proof of a generally available Vertex endpoint.",
        "vertex-maas": "GLM 5, GLM 4.7, DeepSeek V3.2 and Kimi K2 Thinking have announced retirement on 2026-10-21. Recheck publisher-specific replacements before migration.",
        "kimi-k2.6": "Retained packaged configuration; this review did not establish the exact ID in current primary Moonshot documentation.",
    },
)


class _PiModel(BaseModel):
    id: str


class _PiProvider(BaseModel):
    models: tuple[_PiModel, ...] = ()


class _PiCatalog(BaseModel):
    providers: dict[str, _PiProvider]


@cache
def known_model_ids(adapter: str) -> frozenset[str]:
    """Known identifiers, including explicitly documented historical selections."""
    names = MODEL_CATALOG.native_models[adapter]
    if adapter != "pi-cli":
        return names
    raw = resources.files("metaproc.data").joinpath("pi-models.default.json").read_text()
    catalog = _PiCatalog.model_validate_json(raw)
    packaged = {model.id for provider in catalog.providers.values() for model in provider.models}
    # Pi's Vertex MaaS lookup accepts both publisher-qualified IDs and short IDs.
    vertex = catalog.providers.get("vertex-maas")
    if vertex is not None:
        packaged.update(model.id.rsplit("/", 1)[-1] for model in vertex.models)
    return names | packaged


def resolve_model(adapter: str, configured: object) -> str:
    """Default omitted selections; reject unknown explicit selections without substitution."""
    selected = MODEL_CATALOG.defaults[adapter] if configured is None else str(configured)
    if selected not in known_model_ids(adapter):
        raise ValueError(
            f"unknown {adapter} model {selected!r}; choose a known model or update "
            "Metaproc's model catalog. An explicit model is never replaced by the default."
        )
    return selected
