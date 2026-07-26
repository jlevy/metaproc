"""Pydantic models for the pricing.md frontmatter schema.

Replaces the untyped ``dict[str, dict[str, Any]]`` returned by ``load_pricing()``
with structured models that validate on load.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PriceRates(BaseModel):
    """Per-million-token rate card for a single pricing perspective."""

    input_per_1m: float
    output_per_1m: float
    cache_read_per_1m: float = 0.0
    cache_write_per_1m: float = 0.0


class ModelPricing(BaseModel):
    """Pricing entry for a single model, resolved with provider context."""

    actual_price: PriceRates
    list_price: PriceRates | None = None
    provider: str = ""
    cost_source: str = ""
    source_url: str = ""
    list_source_url: str = ""
    last_reviewed: str = ""
    notes: str = ""


class ProviderConfig(BaseModel):
    """A provider block from the pricing frontmatter."""

    models: dict[str, ModelPricing] = Field(default_factory=dict)


class PricingConfig(BaseModel):
    """Top-level pricing configuration from pricing.md frontmatter."""

    last_updated: str = ""
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    def flat_lookup(self) -> dict[str, ModelPricing]:
        """Return a flat ``{model_name: ModelPricing}`` dict across all providers.

        Each entry has its ``provider`` field populated from the provider key.
        """
        result: dict[str, ModelPricing] = {}
        for provider_name, provider_data in self.providers.items():
            for model_name, model_data in provider_data.models.items():
                entry = model_data.model_copy()
                if not entry.provider:
                    entry.provider = provider_name
                result[model_name] = entry
        return result
