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
    reviewed_on=date(2026, 11, 18),
    review_interval_days=30,
    sources={
        "openai": (
            "https://learn.chatgpt.com/docs/models",
            "https://developers.openai.com/api/docs/models",
            "https://developers.openai.com/api/docs/deprecations",
            "https://learn.chatgpt.com/docs/config-file/config-reference",
            "https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/protocol/src/openai_models.rs",
        ),
        "anthropic": (
            "https://platform.claude.com/docs/en/models/overview",
            "https://platform.claude.com/docs/en/about-claude/model-deprecations",
            "https://code.claude.com/docs/en/model-config",
        ),
        "google": (
            "https://aistudio.google.com/docs/models",
            "https://aistudio.google.com/docs/deprecations",
            "https://aistudio.google.com/docs/changelog",
            "https://aistudio.google.com/docs/thinking",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-argon",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-carbon",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-hydrogen",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-6-flash",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-pro",
            "https://github.com/google-gemini/gemini-cli/blob/v0.66.0/packages/core/src/config/models.ts",
        ),
        "pi": (
            "https://github.com/earendil-works/pi/tree/v1.5.2/packages/ai/src/providers",
            "https://github.com/earendil-works/pi/blob/v1.5.2/packages/coding-agent/CHANGELOG.md",
            "https://www.npmjs.com/package/@earendil-works/pi-coding-agent/v/1.5.2",
            "https://pi.dev/schemas/models.schema.json",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/use-open-models",
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models",
            "https://api-docs.deepseek.com/quick_start/pricing/",
            "https://platform.kimi.ai/docs/models",
        ),
    },
    # Defaults are policy choices, not aliases for the newest release. A catalog
    # refresh must not silently migrate existing workloads to another default.
    # The 2026-11-18 review moved codex-cli from gpt-5.5, which Codex retired for
    # ChatGPT sign-in on 2026-10-14, to gpt-6.1-sol, and recorded that the
    # claude-code-cli and pi-cli aliases now resolve to the 5.5 generation.
    defaults={
        "claude-code-cli": "opus",
        "gemini-cli": "gemini-3.1-pro-preview-customtools",
        "codex-cli": "gpt-6.1-sol",
        "pi-cli": "sonnet",
    },
    native_models={
        "claude-code-cli": frozenset(
            {
                "claude-fable-5-2",
                "claude-opus-5-5",
                "claude-sonnet-5-5",
                "claude-haiku-5-5",
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
                "gpt-6.2-astra",
                "gpt-6.2-sol",
                "gpt-6.1-sol",
                "gpt-6.1-terra",
                "gpt-6-astra",
                "gpt-6-sol",
                "gpt-6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.6",
                "gpt-5.5",
                "gpt-5.5-pro",
            }
        ),
        "gemini-cli": frozenset(
            {
                "gemini-4-carbon",
                "gemini-4-argon",
                "gemini-4-hydrogen",
                "gemini-3.8-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-pro-preview",
                "gemini-3.1-pro-preview-customtools",
                "auto",
                "pro",
                "flash",
                "flash-lite",
            }
        ),
        # Built-in model IDs of the pinned Pi 1.5.2 client that Metaproc accepts.
        # IDs Pi does not ship come from pi-models.default.json and are derived in
        # known_model_ids; adding one never needs a second allowlist edit.
        "pi-cli": frozenset(
            {
                # anthropic
                "claude-fable-5-2",
                "claude-opus-5-5",
                "claude-sonnet-5-5",
                "claude-haiku-5-5",
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
                # openai
                "gpt-6.1-sol",
                "gpt-6.1-terra",
                "gpt-6-astra",
                "gpt-6-sol",
                "gpt-6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                # google-vertex
                "gemini-4-argon",
                "gemini-3.8-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-pro-preview",
                "gemini-3.1-pro-preview-customtools",
                # deepseek
                "deepseek-flash",
                "deepseek-v4-pro",
            }
        ),
    },
    lifecycle_notes={
        "claude-fable-5-2": "Current Anthropic ID (2026-11-09). Claude Code requires at least 2.1.321, adopted under a reviewed cool-off exemption. Pi requires at least 1.5.1; the pinned 1.5.2 ships it.",
        "claude-opus-5-5": "Current Anthropic ID (2026-09-22). Claude Code requires at least 2.1.280.",
        "claude-sonnet-5-5": "Current Anthropic ID (2026-09-28). Claude Code requires at least 2.1.284.",
        "claude-haiku-5-5": "Current Anthropic ID (2026-10-07). Claude Code requires at least 2.1.293.",
        "opus": "Moving alias: Opus 5.5 in the pinned Claude Code and Pi 1.5.2 clients (Opus 5 on Claude Code 2.1.234). The claude-code-cli default; the move is recorded, not silent.",
        "sonnet": "Moving alias: Sonnet 5.5 in the pinned Claude Code and Pi clients (Sonnet 5 before). The pi-cli default.",
        "fable": "Moving alias: Fable 5.2 from Claude Code 2.1.321 and Pi 1.5.1; the pinned Claude Code and Pi 1.5.2 clients send Fable 5.2.",
        "haiku": "Moving alias: Haiku 5.5 in the pinned Claude Code and Pi clients (Haiku 4.5 before).",
        "gpt-6.1-sol": "Current OpenAI ID (2026-09-29) and the codex-cli default since the 2026-11-18 review; listed in the pinned Codex 0.166.0 model presets.",
        "gpt-6.2-astra": "Released 2026-11-17, after Codex 0.166.0; absent from its bundled presets. Codex forwards the ID; the API-key route was probed 2026-11-18, ChatGPT sign-in availability is unverified.",
        "gpt-6.2-sol": "Released 2026-11-17, after Codex 0.166.0; absent from its bundled presets. Codex forwards the ID; the API-key route was probed 2026-11-18, ChatGPT sign-in availability is unverified.",
        "gpt-6-astra": "Codex presets mark it as requiring client 0.153.0 or later; the 0.166.0 pin satisfies that.",
        "gpt-5.6": "Documented Sol alias; retain the requested alias in argv.",
        "gpt-5.5": "Retired from Codex with ChatGPT sign-in on 2026-10-14; API-key use only. Former codex-cli default.",
        "gpt-5.5-pro": "API-key use only; API existence does not establish Codex account access.",
        "gemini-4-argon": "GA 2026-10-13; Vertex availability until at least 2027-10-13. Thinking low, medium, high; minimal returns HTTP 400. Unknown to gemini-cli through 0.69.0-preview.0, which forwards it unchanged without a thinking-level alias.",
        "gemini-4-carbon": "GA 2026-11-10; Google's strongest model, paid tier only. Thinking low, medium, high. Unknown to gemini-cli through 0.69.0-preview.0, which forwards it unchanged.",
        "gemini-4-hydrogen": "GA 2026-11-12; Google's recommended upgrade from 3.8 Flash. Thinking low through xhigh, default high. Unknown to gemini-cli through 0.69.0-preview.0, which forwards it unchanged.",
        "gemini-3.6-flash": "Retained on the Gemini API (GA, no shutdown announced). Vertex retires it 2026-11-19 with gemini-3.8-flash as the replacement; shipped profiles no longer use it.",
        "gemini-3.5-flash-lite": "Current Flash-Lite. gemini-cli 0.62.0 and later send it for gemini-3.1-flash-lite and flash-lite.",
        "gemini-3.1-pro-preview-customtools": "Public-preview endpoint on the Gemini API and Vertex (released 2026-02-23); 1,048,576-token context. The gemini-cli default.",
        "flash": "Moving gemini-cli alias: gemini-3.8-flash in 0.66.0.",
        "flash-lite": "Moving gemini-cli alias: gemini-3.5-flash-lite in 0.66.0.",
        "pro": "Moving gemini-cli alias: gemini-3.1-pro-preview on Vertex, -customtools with an API key, in 0.66.0.",
        "auto": "Moving gemini-cli alias: resolves like pro in 0.66.0.",
        "removed-2026-11-18": "Dropped as retired, deprecated, or superseded: gemini-3.7-flash, gemini-3.5-flash, gemini-3.1-flash-lite, gemini-3-flash-preview, gemini-3-pro-preview, gemini-3.1-flash-lite-preview, auto-gemini-3, gpt-5.4, gpt-5.4-mini, gpt-5.3-codex-spark, and the six Vertex MaaS IDs retired 2026-10-21. gemini-cli 0.62.0 and later rewrite gemini-3.5-flash and gemini-3-flash to gemini-3.8-flash, so those IDs could not be served as requested.",
        "pi-cli": "Pinned Pi 1.5.2, adopted under a reviewed cool-off exemption because claude-fable-5-2 needs Pi 1.5.1 or later. Pi resolves sonnet, opus, haiku and fable to the newest undated match: claude-sonnet-5-5, claude-opus-5-5, claude-haiku-5-5 and claude-fable-5-2 (claude-sonnet-5, claude-opus-5, claude-haiku-4-5 and claude-fable-5 on 0.84.2).",
        "zai-org/glm-5.2-maas": "Vertex AI MaaS preview (2026-08-24), global endpoint only, available until at least 2026-11-30; replaces the retired GLM-5 and GLM-4.7 MaaS IDs. Pi also accepts the short ID glm-5.2-maas.",
        "deepseek-ai/deepseek-v4.1-flash-maas": "Vertex AI MaaS preview (2026-10-14), global endpoint only, available until at least 2026-11-28; replaces the retired DeepSeek V3.2 MaaS ID.",
        "deepseek-ai/deepseek-v4.1-flash-1017-maas": "Vertex AI MaaS preview (2026-11-16), global endpoint only; Vertex publishes no price yet, so Pi reports no cost for it.",
        "deepseek-flash": "DeepSeek's current Flash name, served by DeepSeek-V4.1-Flash-1017. The legacy deepseek-v4-flash name is a retired alias and is no longer accepted.",
        "kimi-k3.1-pro": "Moonshot's current flagship (1,048,576-token context), on Metaproc's moonshot route; kimi-k3 is the previous generation.",
        "kimi-k2.6": "Exact Moonshot ID confirmed; closed to new users from 2026-11-01, deprecation expected late December 2026. Retained on Pi's moonshot route for existing accounts until then.",
        "pi-removed-2026-11-18": "Dropped from the pi-cli catalog as retired, retiring, or superseded: deepseek-v4-flash, gpt-5.4-mini, gpt-5.4-nano, gemini-3.7-flash, gemini-3.6-flash (Vertex retires it 2026-11-19), gemini-3.5-flash, gemini-3.1-flash-lite, gemini-3-flash-preview, and the six Vertex MaaS IDs retired 2026-10-21 with their short forms.",
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
