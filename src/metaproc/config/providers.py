"""Single source of truth for LLM provider metadata.

Adding a new direct-API provider should be a one-place change: add a
`ProviderSpec` entry to `PROVIDERS` and the rest of the codebase
(`pi_cli.check_auth`, `batch_backend.GCP_SECRET_REFS`,
`auth_check._infer_pi_provider`, `test_usage._PROVIDER_LABELS`,
`settings.PI_VALID_PROVIDERS`, the `pricing.md` block, the
`pi-models.default.json` block) picks the new entry up automatically or
will fail loudly via the existing parity tests.

See `metaproc/docs/runbooks/adding-a-new-llm-provider.runbook.md` for the full
checklist (env vars, Secret Manager refs, pricing, catalog, smoke).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from metaproc.config.env_vars import MetaprocEnv


def _never(_name: str) -> bool:
    return False


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata for a pi-cli provider that metaproc actively manages."""

    name: str
    """Provider name as used by pi-cli (matches the key in pi-models.default.json)."""

    label: str
    """Human-readable label (used in pricing.md tables, status output)."""

    api_key_env: MetaprocEnv | None = None
    """Env var that holds the API key. None for providers that auth via ADC
    (`google-vertex`) or a separately-resolved access token (`vertex-maas`)."""

    gcp_secret_ref_env: MetaprocEnv | None = None
    """Env var that names a GCP Secret Manager resource for Batch worker
    injection. None for providers that don't ship through Batch via a key
    env var (e.g. `vertex-maas` uses the worker's service account)."""

    gcp_secret_description: str | None = None
    """One-line description for the Batch secret-injection table."""

    model_name_match: Callable[[str], bool] = field(default=_never)
    """Predicate over a lowercased model name. Used by
    `auth_check._infer_pi_provider` to decide which provider should run a
    live check for a given variant when the operator did not pass an
    explicit `--provider`."""

    requires_gcp_token: bool = False
    """When True, pi-cli live checks for this provider must inject a fresh
    GCP access token via `--api-key`. Currently only `vertex-maas`."""

    def credentials_present(self) -> bool:
        if self.api_key_env is None:
            return False
        return bool(self.api_key_env.read_str(default=None))


PROVIDERS: tuple[ProviderSpec, ...] = (
    # vertex-maas comes first so any "*-maas" model ID (e.g.
    # `deepseek-v3.2-maas`, `moonshotai/kimi-k2-thinking-maas`) infers the
    # Vertex MaaS provider rather than the direct-API provider whose
    # prefix would otherwise match.
    ProviderSpec(
        name="vertex-maas",
        label="Vertex MaaS",
        # Auth via short-lived gcloud access token resolved at dispatch time.
        model_name_match=lambda n: "maas" in n,
        requires_gcp_token=True,
    ),
    ProviderSpec(
        name="anthropic",
        label="Anthropic",
        api_key_env=MetaprocEnv.ANTHROPIC_API_KEY,
        model_name_match=lambda n: n.startswith("claude-") or n in {"opus", "sonnet", "haiku"},
    ),
    ProviderSpec(
        name="openai",
        label="OpenAI",
        api_key_env=MetaprocEnv.OPENAI_API_KEY,
        gcp_secret_ref_env=MetaprocEnv.METAPROC_GCP_SECRET_OPENAI_API_KEY,
        gcp_secret_description=("OpenAI API key (codex-cli Vehicle A + pi-cli openai provider)"),
        model_name_match=lambda n: n.startswith(("gpt-", "o3", "o4-")),
    ),
    ProviderSpec(
        name="deepseek",
        label="DeepSeek",
        api_key_env=MetaprocEnv.DEEPSEEK_API_KEY,
        gcp_secret_ref_env=MetaprocEnv.METAPROC_GCP_SECRET_DEEPSEEK_API_KEY,
        gcp_secret_description=("DeepSeek API key (pi-cli deepseek provider, V4 direct API)"),
        model_name_match=lambda n: n.startswith("deepseek-v"),
    ),
    ProviderSpec(
        name="moonshot",
        label="Moonshot",
        api_key_env=MetaprocEnv.MOONSHOT_API_KEY,
        gcp_secret_ref_env=MetaprocEnv.METAPROC_GCP_SECRET_MOONSHOT_API_KEY,
        gcp_secret_description=(
            "Moonshot API key (pi-cli moonshot provider, Kimi K2.6 direct API)"
        ),
        model_name_match=lambda n: n.startswith("kimi-"),
    ),
    ProviderSpec(
        name="google",
        label="Google",
        api_key_env=MetaprocEnv.GEMINI_API_KEY,
        # Direct Google AI Studio path; google-vertex (Vertex AI) handles the
        # gemini-* IDs in our setup, so model_name_match stays empty.
    ),
    ProviderSpec(
        name="google-vertex",
        label="Google Vertex",
        # Auth via Application Default Credentials.
        model_name_match=lambda n: n.startswith(("gemini-", "google/gemini")),
    ),
)
"""Registry of LLM providers metaproc can dispatch to via pi-cli.

Order matters for `infer_provider`: the first spec whose `model_name_match`
returns True wins. Place more-specific predicates earlier."""


_PI_EXTRA_VALID_PROVIDER_NAMES: frozenset[str] = frozenset(
    {
        # Pi-cli supports these vendor-cloud providers via its built-in
        # catalog. Metaproc does not actively wire credentials or Batch
        # injection for them today, so they live here rather than as full
        # ProviderSpec entries.
        "vertex",
        "azure",
        "bedrock",
    }
)


def provider_by_name(name: str) -> ProviderSpec | None:
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None


def infer_provider(model_name: str | None) -> ProviderSpec | None:
    """Return the provider that should serve `model_name`, or None.

    Used by `auth-check --live` when the operator did not pass an explicit
    `--provider`. Without this inference, pi-cli silently falls back to its
    default provider and live checks false-green against the wrong model.
    """
    name = (model_name or "").lower()
    if not name:
        return None
    for spec in PROVIDERS:
        if spec.model_name_match(name):
            return spec
    return None


def gcp_secret_refs() -> tuple[tuple[str, str, str], ...]:
    """Return (plaintext_env_name, secret_ref_env_name, description) for
    every provider that ships Batch credentials via Secret Manager."""
    refs: list[tuple[str, str, str]] = []
    for spec in PROVIDERS:
        if (
            spec.api_key_env is None
            or spec.gcp_secret_ref_env is None
            or spec.gcp_secret_description is None
        ):
            continue
        refs.append(
            (spec.api_key_env.name, spec.gcp_secret_ref_env.name, spec.gcp_secret_description)
        )
    return tuple(refs)


def pi_valid_provider_names() -> frozenset[str]:
    """All provider name tokens that the pi-cli adapter should accept.

    Combines the registry-managed providers with the pi-cli built-in
    cloud-vendor names that metaproc does not actively wire."""
    return frozenset(spec.name for spec in PROVIDERS) | _PI_EXTRA_VALID_PROVIDER_NAMES


def provider_labels() -> dict[str, str]:
    """name → display label, used by the pricing comparison table tests
    and any operator-facing rollups."""
    return {spec.name: spec.label for spec in PROVIDERS}


def providers_with_api_keys() -> tuple[ProviderSpec, ...]:
    """Providers whose `check_auth` participates in env-var detection."""
    return tuple(spec for spec in PROVIDERS if spec.api_key_env is not None)
