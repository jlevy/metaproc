"""Typed registry of every environment variable consumed by Metaproc.

Metaproc is a generic process-orchestration engine; this registry holds only
workflow-agnostic variables. Workflow plugins own their domain-specific
registries. Metaproc treats those variables as opaque pass-through values.

Every environment-variable read in this package should go through
:class:`MetaprocEnv`. The coverage test at
``tests/test_env_vars_coverage.py`` enforces that contract for ``src/metaproc``.

Each member is declared with one of the :func:`~metaproc.config.env_enum.real` /
:func:`~metaproc.config.env_enum.tunable` /
:func:`~metaproc.config.env_enum.secret` /
:func:`~metaproc.config.env_enum.optional` factories; the factory carries
the description, kind, and illustration value inline with the member, so
each declaration reads left-to-right without a sidecar dict.

Grouping is by block comment — source order is the ``.env.example`` order
emitted by ``metaproc env --template`` (Phase 3).
"""

from __future__ import annotations

from metaproc.config.env_enum import EnvEnum, optional, real, secret, tunable


class MetaprocEnv(EnvEnum):
    """Environment variables read by Metaproc itself.

    Workflow-specific env vars (run-root settings, plugin knobs) live in their
    owning workflow registry, not here. Metaproc treats them as opaque
    pass-through values when they show up in `os.environ`.
    """

    # ── GCP infrastructure (required for full-cloud Batch execution) ──
    METAPROC_GCP_PROJECT = tunable(
        "GCP project ID for Batch / Compute operations.",
        "<your-gcp-project-id>",
    )
    METAPROC_GCP_SERVICE_ACCOUNT = tunable(
        "Explicit service account for Batch VMs. Required for every job that binds "
        "Secret Manager values; dispatch refuses secret-bearing jobs without it.",
        "<worker-sa>@<your-gcp-project-id>.iam.gserviceaccount.com",
    )
    METAPROC_GCP_CONTAINER_IMAGE = tunable(
        "Docker image URI for the GCP Batch agent.",
        "<region>-docker.pkg.dev/<your-gcp-project-id>/<your-repo>/agent:latest",
    )
    METAPROC_GCP_REGION = tunable("GCP region for Batch jobs.", "us-central1")
    METAPROC_GCP_NETWORK = optional(
        "VPC network for Batch job VMs. Uses default network when unset."
    )
    METAPROC_GCP_SUBNETWORK = optional(
        "VPC subnetwork for Batch job VMs. Uses default subnet when unset."
    )
    METAPROC_GCP_FILESTORE_SERVER = tunable(
        "Filestore NFS server IP for shared run artifact storage.", "10.0.0.10"
    )
    METAPROC_GCP_FILESTORE_SHARE = tunable(
        "NFS share name on the Filestore instance.", "/<your-share-name>"
    )
    METAPROC_GCP_FILESTORE_MOUNT_PATH = tunable(
        "Local mount point for the Filestore share on worker VMs.", "/mnt/filestore"
    )
    METAPROC_GCP_BOOT_DISK_GB = tunable("Boot disk size (GB) for worker VMs.", "50")
    METAPROC_GCP_MAX_RUN_DURATION_S = tunable("Max run duration (seconds) per Batch task.", "28800")
    METAPROC_GCP_TASK_CPU_MILLI = optional("Override CPU milli-cores per Batch task.")
    METAPROC_GCP_TASK_MEMORY_MIB = optional("Override memory MiB per Batch task.")
    METAPROC_GCS_BUCKET = tunable(
        "GCS bucket for wheel and workspace uploads used by cloud dispatch.",
        "<your-gcs-bucket>",
    )
    METAPROC_GCP_RUN_CMD = optional(
        "Serialized JSON command used by the `gcp-run` entrypoint. Set by the dispatcher."
    )

    # ── Repo resolution for cloud workers ──
    METAPROC_REPO_URL = tunable(
        "Git repo URL that worker containers clone to obtain project code.",
        "https://github.com/<your-org>/<your-repo>.git",
    )
    METAPROC_RUN_BRANCH = tunable(
        "Git branch workers check out. Typically your current branch or `main`.", "main"
    )
    METAPROC_REPO_SPARSE_PATHS = optional(
        "Comma-separated repo-relative paths for worker sparse checkout. Empty clones "
        "the complete repository."
    )
    METAPROC_REPO_PACKAGE_PATH = tunable(
        "Repo-relative path containing Metaproc's pyproject.toml for editable remote installs.",
        ".",
    )
    METAPROC_WORKSPACE_PACKAGES = optional(
        "Comma-separated repo-relative Python package paths to install after workspace sync."
    )
    METAPROC_BUNDLED_REPO_DIR = optional(
        "Optional image-bundled repository root used when no runtime workspace is supplied."
    )
    METAPROC_WHEEL_GCS = optional(
        "GCS path to a prebuilt Metaproc wheel. When set, workers verify and install "
        "this wheel over the image-baked version.",
        "gs://<your-gcs-bucket>/wheels/metaproc-<version>-py3-none-any.whl",
    )
    METAPROC_WHEEL_SHA256 = optional(
        "Required SHA-256 digest for METAPROC_WHEEL_GCS artifact verification.",
        "<64-hex-character-sha256>",
    )
    METAPROC_WORKSPACE_GCS = optional(
        "GCS path to a consumer workspace tarball. When set, workers verify and "
        "extract it instead of cloning the configured repository.",
        "gs://<your-gcs-bucket>/workspaces/<your-plugin>.tar.gz",
    )
    METAPROC_WORKSPACE_SHA256 = optional(
        "Required SHA-256 digest for METAPROC_WORKSPACE_GCS artifact verification.",
        "<64-hex-character-sha256>",
    )

    METAPROC_PI_MODELS_JSON = optional(
        "JSON blob of PI model configs injected during worker bootstrap."
    )

    # ── Worker / orchestrator entrypoint vars (set by dispatcher) ──
    METAPROC_GCP_ORCHESTRATOR = optional(
        "Dispatcher-owned admission marker for the inner full-cloud orchestrator process."
    )
    METAPROC_WORKER_ITEMS = optional("Serialized JSON list of items this worker should process.")
    METAPROC_WORKER_ID = optional("Identifier of the current worker instance (runtime-injected).")
    METAPROC_PROCESS_SPEC = optional(
        "Path to the .process.md spec file inside the worker container (canonical)."
    )
    METAPROC_PROCESS_DIR = optional(
        "Legacy: path to the process-definition directory. Retained for read-only "
        "compatibility with pre-rename run-configs; new dispatch uses METAPROC_PROCESS_SPEC."
    )
    METAPROC_STEP = optional("Step name this worker should execute.")
    METAPROC_VARS = optional("Serialized JSON dict of per-run variable overrides.")
    METAPROC_VARIANT = optional(
        "Variant label passed through to worker / orchestrator entrypoints."
    )
    METAPROC_ADAPTER_CONFIG = optional(
        "Serialized JSON adapter configuration passed to worker entrypoints."
    )
    METAPROC_MAX_RETRIES = optional("Max retry count for failed items inside a worker.")
    METAPROC_ADAPTER_STRICT = optional(
        "When truthy, adapter validation is strict — unknown fields raise."
    )
    METAPROC_NUM_WORKERS = optional("Number of worker VMs for orchestrator-launched runs.")
    METAPROC_MACHINE_TYPE = optional(
        "GCE machine type for orchestrator-launched worker VMs.", "e2-standard-4"
    )
    METAPROC_GCP_MACHINE_TYPE = optional(
        "GCE machine type override for full-cloud fan-out workers. Distinct from "
        "METAPROC_MACHINE_TYPE, which scopes the orchestrator VM.",
        "e2-highmem-8",
    )
    METAPROC_SPOT = optional(
        "When truthy, orchestrator launches workers as Spot / preemptible VMs."
    )
    METAPROC_SKIP_STEPS = optional("CSV of step names to skip during orchestrator runs.")
    METAPROC_FROM_STEP = optional("Start orchestrator run from this step (inclusive).")
    METAPROC_ONLY_STEP = optional("Run only this single step in the orchestrator run.")
    METAPROC_FORCE = optional("When truthy, orchestrator re-runs already-completed steps.")
    METAPROC_CONTINUE_ON_ERROR = optional("When truthy, orchestrator continues past step failures.")
    METAPROC_ITEM_CONTEXTS = optional("Inline JSON item-context map.")
    METAPROC_ITEM_CONTEXTS_FILE = optional(
        "Path to a file containing item contexts (alternative to inline JSON)."
    )

    # ── Secrets (.env-backed or Secret Manager) ──
    GCP_CREDENTIALS_BASE64 = secret(
        "Base64-encoded service-account JSON for GCP auth. Alternative to ADC.",
        "REPLACE_WITH_BASE64_ENCODED_SERVICE_ACCOUNT_JSON",
    )
    GOOGLE_APPLICATION_CREDENTIALS = optional(
        "Path to a GCP service-account JSON file for Application Default Credentials."
    )
    GH_TOKEN = secret(
        "GitHub personal access token for gh-backed workflows.", "REPLACE_WITH_GITHUB_TOKEN"
    )
    GH_PROMPT_DISABLED = real("When truthy, the `gh` CLI skips interactive prompts.", "1")
    PERPLEXITY_API_KEY = secret(
        "Perplexity API key used by the web search provider.", "your_api_key_here"
    )
    ANTHROPIC_API_KEY = secret("Anthropic API key for direct Claude adapter use.")
    OPENAI_API_KEY = secret(
        "OpenAI API key used by the PI CLI adapter and the codex-cli adapter "
        "(Vehicle A, API-key mode)."
    )
    DEEPSEEK_API_KEY = secret(
        "DeepSeek API key for the pi-cli `deepseek` provider (DeepSeek V4 family, direct API)."
    )
    MOONSHOT_API_KEY = secret(
        "Moonshot API key for the pi-cli `moonshot` provider (Kimi K2.6, direct API)."
    )
    GEMINI_API_KEY = secret("Gemini API key for direct (non-Vertex) Gemini adapter use.")
    GOOGLE_API_KEY = secret("Google API key for direct Gemini adapter fallback.")
    GOOGLE_GENAI_USE_VERTEXAI = optional(
        "When truthy, Gemini adapter routes through Vertex AI instead of the direct API."
    )
    GOOGLE_CLOUD_PROJECT = optional("GCP project for Vertex AI model routing.")
    METAPROC_GCP_SECRET_GH_TOKEN = optional(
        "GCP Secret Manager ref that provides GH_TOKEN to Batch workers.",
        "projects/<your-gcp-project-id>/secrets/<secret-name>/versions/latest",
    )
    METAPROC_GCP_SECRET_CLAUDE_CREDS = optional(
        "GCP Secret Manager ref that provides CLAUDE_CODE_CREDS_JSON to Batch workers."
    )
    METAPROC_GCP_SECRET_OPENAI_API_KEY = optional(
        "GCP Secret Manager ref that provides OPENAI_API_KEY to Batch workers "
        "(codex-cli Vehicle A + pi-cli openai provider)."
    )
    METAPROC_GCP_SECRET_DEEPSEEK_API_KEY = optional(
        "GCP Secret Manager ref that provides DEEPSEEK_API_KEY to Batch workers "
        "(pi-cli deepseek provider, DeepSeek V4 direct API)."
    )
    METAPROC_GCP_SECRET_MOONSHOT_API_KEY = optional(
        "GCP Secret Manager ref that provides MOONSHOT_API_KEY to Batch workers "
        "(pi-cli moonshot provider, Kimi K2.6 direct API)."
    )
    CLAUDE_CODE_CREDS_JSON = secret(
        "Claude Code OAuth credential blob injected into Batch workers from Secret Manager."
    )
    METAPROC_GCP_SECRET_CODEX_CREDS = optional(
        "GCP Secret Manager ref that provides CODEX_CREDS_JSON to Batch workers."
    )
    CODEX_CREDS_JSON = secret(
        "Codex CLI ChatGPT-OAuth credential blob (contents of ~/.codex/auth.json) "
        "injected into Batch workers from Secret Manager. Vehicle B only — API-key "
        "mode arrives via OPENAI_API_KEY instead."
    )
    CODEX_HOME = optional(
        "Override path for the codex-cli home directory (default ~/.codex). "
        "Honored by codex-cli itself, the codex adapter's check_auth / "
        "prepare_env / bootstrap, and the metaproc codex-auth CLI. Used by "
        "Batch workers to scope per-task auth.json materialization."
    )
    CLAUDE_CONFIG_DIR = optional(
        "Override path for Claude Code CLI config directory (default ~/.claude). "
        "Used by credential-pool slots to scope per-item OAuth materialization."
    )
    METAPROC_SKIP_CLAUDE_VERSION_CHECK = optional(
        "When truthy, bypass the `claude --version` pin check at adapter "
        "build_command time. The pin is a runtime drift guard against "
        "local/cloud version skew; set this in CI / test environments where "
        "the claude binary isn't on PATH and the dry-run paths never invoke it."
    )
    METAPROC_SKIP_CODEX_VERSION_CHECK = optional(
        "When truthy, bypass the `codex --version` pin check at adapter "
        "build_command time. Use only in CI or tests that construct commands "
        "without launching Codex."
    )
    METAPROC_SKIP_GEMINI_VERSION_CHECK = optional(
        "When truthy, bypass the `gemini --version` pin check at plan-time "
        "preflight and adapter build_command time. Use only in CI or tests "
        "where the gemini binary isn't on PATH."
    )
    METAPROC_SKIP_PI_VERSION_CHECK = optional(
        "When truthy, bypass the `pi --version` pin check at plan-time "
        "preflight and adapter build_command time. Distinct from pi's own "
        "internal PI_SKIP_VERSION_CHECK; use only in CI or tests where the pi "
        "binary isn't on PATH."
    )

    # ── Auth credential pool (plan-2026-04-21-auth-credential-pool.md) ──
    METAPROC_AUTH_POOL = optional(
        "Owner of the credential pool to read. Defaults to $USER. "
        "Set this to share a single operator's pool across CI accounts."
    )
    METAPROC_AUTH_BACKEND = optional(
        "Storage backend for `metaproc auth`: 'local' or 'gcp-secret-manager'. "
        "Defaults to gcp-secret-manager in Phase 1; flips to local for "
        "non-cloud flows once Phase 2b lands."
    )
    METAPROC_AUTH_POOL_RUN = optional(
        "Set to '1' by the slot coordinator to tell "
        "ClaudeCodeCliAdapter.bootstrap to no-op — the per-item slot path "
        "owns credential materialization. Do not set this by hand."
    )
    METAPROC_AUTH_POOL_LOCK_DIR = optional(
        "Override directory for Vehicle B per-label lock dirs (Phase 5 safe mode). "
        "Defaults to ~/.metaproc/auth-pool/locks. Set this to a shared filestore "
        "path when running cloud workers that need cross-host serialization."
    )
    METAPROC_AUTH_POOL_LOCK_TIMEOUT_S = optional(
        "Override the V-B safe-mode per-label lock timeout (integer seconds). "
        "Defaults to 300 (5 min). Lower for diagnostic dispatches that should "
        "fail-fast rather than queue behind another in-flight V-B lease."
    )
    METAPROC_PREFLIGHT_MIN_DISK_GB = optional(
        "Override the preflight disk-space minimum (float GB). Defaults to "
        "5.0. Lower this only when "
        "the operator has accepted the risk of a mid-run fill on a near-full "
        "disk."
    )
    # Orchestrator → entrypoint passthrough for `run-process --auth-*` flags
    # (mirrors the METAPROC_VARIANT / METAPROC_FORCE / METAPROC_SKIP_STEPS
    # passthrough pattern). Set by orchestrator_dispatch; consumed by
    # orchestrator_entrypoint to build the inner `run-process` command.
    METAPROC_AUTH_ACCOUNT = optional(
        "Adapter type to lease pool credentials from on the inner run-process "
        "command (e.g. claude-code-cli). Forwarded as --auth-account."
    )
    METAPROC_AUTH_FALLBACK_POLICY = optional(
        "Cross-adapter fallback policy passed through as --auth-fallback-policy "
        "(none|same-provider|cross-provider|both)."
    )
    METAPROC_AUTH_POLICY = optional(
        "Label selection policy passed through as --auth-policy "
        "(priority-order|round-robin|least-active). When unset, defaults to "
        "round-robin for ≥ 2 included labels, else priority-order. "
        "See plan-2026-05-03-auth-observability-and-load-balancing.md."
    )
    METAPROC_AUTH_INCLUDE_LABELS = optional(
        "CSV of pool labels to restrict selection to. Forwarded as repeated "
        "--auth-include-labels flags."
    )
    METAPROC_AUTH_EXCLUDE_LABELS = optional(
        "CSV of pool labels to exclude. Forwarded as repeated --auth-exclude-labels flags."
    )
    METAPROC_AUTH_CROSS_QUOTA_GROUP = optional(
        "Set to 'false' to disable cross-quota-group expansion on 429 cooling. "
        "When true (default), a 429 on a label adds every sibling label sharing the "
        "failing label's quota_group to pool_exclude. Useful for diagnostic dispatches "
        "against a single account where cross-org expansion would empty the pool."
    )

    # ── Concurrency / quota knobs ──
    METAPROC_DEFAULT_MAX_CONCURRENCY = tunable(
        "Default per-step max concurrency. Edit here when Vertex MaaS DSQ quota changes.",
        "25",
    )
    METAPROC_DEFAULT_NUM_WORKERS = tunable(
        "Default number of workers per step when not specified per-step.", "1"
    )
    METAPROC_MAX_CONCURRENCY = optional(
        "Per-run max concurrency override (passed through to worker entrypoint)."
    )
    METAPROC_INITIAL_CONCURRENCY = optional(
        "Per-run starting concurrency override; ramps up to max via runpool pressure checks."
    )

    # ── Run artifact roots (generic) ──
    # Workflow-specific run-root settings and external-tool knobs live in their
    # owning consumer registries.
    # Metaproc itself only knows the generic RUNS_DIR runtime value.
    RUNS_DIR = optional(
        "Runtime run-artifact parent directory passed to Metaproc. Required at "
        "workflow launch time; set explicitly or derive from workflow run settings.",
        "/absolute/path/to/runs",
    )
    RUN_ID = optional("Current run identifier (runtime-injected; used for placeholder expansion).")
    RUN_ID_TEMPLATE = optional("Template string for auto-generating run IDs.")
    VARIANT = optional("Run variant label for placeholder expansion (runtime-injected).")
    METAPROC_OPERATOR = optional("Operator name for run metadata. Falls back to USER / USERNAME.")

    # ── metabrowser plugin discovery ──
    #
    # metabrowser auto-loads built-in plugins (markdown / text / agent-log /
    # unknown-jsonl / binary) and Python entry-point plugins (e.g. the
    # `metaproc.metabrowser_plugin` subpackage that ships in metaproc's
    # wheel). To add custom plugin directories, list their parent
    # directories here (os.pathsep-separated, like PATH); each subdirectory
    # containing manifest.toml will load. This is the only opt-in surface —
    # auto-discovery from the served root is intentionally disabled
    # (trust model: served data must not run JS in the page).
    METABROWSER_PLUGINS_DIRS = optional(
        "metabrowser plugin parents to scan, os.pathsep-separated. Each "
        "subdirectory containing manifest.toml is loaded. Built-ins and "
        "Python entry-point plugins (e.g. metaproc's metabrowser_plugin "
        "subpackage) load automatically; this is the opt-in surface for "
        "ad-hoc / workspace-local plugins. Auto-discovery from the served root is "
        "intentionally NOT a source.",
        "/path/to/my-plugins:/path/to/team-plugins",
    )

    # ── System / runtime-injected (included for registry completeness) ──
    USER = optional("Unix username. Read as a fallback for METAPROC_OPERATOR.")
    USERNAME = optional("Windows username. Read as a fallback for METAPROC_OPERATOR.")
    VIRTUAL_ENV = optional("Path to the active virtualenv (set by the venv activator).")
    BATCH_TASK_INDEX = optional("GCP Batch task index. Set by the Batch runtime.")
    NO_COLOR = optional("Standard convention: when set, disables colored terminal output.")
    CI = optional("Standard convention: set by CI systems; triggers non-interactive defaults.")


SECRET_VARS: frozenset[MetaprocEnv] = frozenset(
    {
        MetaprocEnv.METAPROC_GCP_SECRET_GH_TOKEN,
        MetaprocEnv.METAPROC_GCP_SECRET_CLAUDE_CREDS,
        MetaprocEnv.METAPROC_GCP_SECRET_CODEX_CREDS,
        MetaprocEnv.GCP_CREDENTIALS_BASE64,
        MetaprocEnv.GH_TOKEN,
        MetaprocEnv.PERPLEXITY_API_KEY,
        MetaprocEnv.ANTHROPIC_API_KEY,
        MetaprocEnv.OPENAI_API_KEY,
        MetaprocEnv.DEEPSEEK_API_KEY,
        MetaprocEnv.MOONSHOT_API_KEY,
        MetaprocEnv.GEMINI_API_KEY,
        MetaprocEnv.GOOGLE_API_KEY,
        MetaprocEnv.CLAUDE_CODE_CREDS_JSON,
        MetaprocEnv.CODEX_CREDS_JSON,
    }
)
"""Registry members whose current values should be masked in operator-facing output.

Membership is explicit rather than guessed from the name. ``METAPROC_GCP_SECRET_*``
Secret Manager *refs* are worth masking (they leak project structure) but
render in ``.env.example`` as ``OPTIONAL`` because they point at secrets
rather than being secrets themselves — hence this frozenset is maintained
independently of ``EnvMeta.kind``.
"""
