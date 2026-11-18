"""Execution profile registry models.

Execution profiles are domain-neutral adapter runtime profiles. They select
an adapter, model/provider config, generic capability metadata, and scheduler
resource hints without encoding workflow-specific concepts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaproc.io import from_yaml_string

_SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "credential",
        "credentials",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
    }
)

_SECRET_VALUE_PREFIXES = (
    "sk-",
    "ghp_",
    "gho_",
    "ghu_",
    "github_pat_",
)


class ExecutionProfileResources(BaseModel):
    """Scheduler-owned resource hints for a profile."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    estimated_process_rss_mb: int | None = None
    estimated_process_rss_bytes: int | None = None
    initial_memory_budget_fraction: float | None = None
    max_concurrency_hint: int | None = None
    host_max_concurrency: int | None = None
    # Per-profile override for the runpool's min_concurrency floor. Profiles with very heavy
    # per-agent RSS may need to raise this floor higher to avoid degenerate
    # over-clamping; profiles with very light agents can lower to 1 if the
    # operator accepts the degenerate-state risk.
    min_concurrency: int | None = None

    @model_validator(mode="after")
    def validate_resources(self) -> ExecutionProfileResources:
        if (
            self.estimated_process_rss_mb is not None
            and self.estimated_process_rss_bytes is not None
        ):
            msg = "resources must not set both estimated_process_rss_mb and estimated_process_rss_bytes"
            raise ValueError(msg)
        if self.estimated_process_rss_mb is not None and self.estimated_process_rss_mb <= 0:
            msg = "estimated_process_rss_mb must be positive"
            raise ValueError(msg)
        if self.estimated_process_rss_bytes is not None and self.estimated_process_rss_bytes <= 0:
            msg = "estimated_process_rss_bytes must be positive"
            raise ValueError(msg)
        if (
            self.initial_memory_budget_fraction is not None
            and not 0 < self.initial_memory_budget_fraction <= 1
        ):
            msg = "initial_memory_budget_fraction must be > 0 and <= 1"
            raise ValueError(msg)
        if self.max_concurrency_hint is not None and self.max_concurrency_hint <= 0:
            msg = "max_concurrency_hint must be positive"
            raise ValueError(msg)
        if self.host_max_concurrency is not None and self.host_max_concurrency <= 0:
            msg = "host_max_concurrency must be positive"
            raise ValueError(msg)
        if self.min_concurrency is not None and self.min_concurrency < 1:
            msg = "min_concurrency must be >= 1"
            raise ValueError(msg)
        return self


class ExecutionProfile(BaseModel):
    """Named adapter runtime profile definition."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="forbid")

    schema_: str = Field(default="metaproc:ExecutionProfile/0.1", alias="schema")
    extends: str | None = None
    status: Literal["tested", "experimental", "deprecated", "blocked"] | None = None
    adapter: str | None = None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    thinking: str | None = None
    capabilities: dict[str, object] = Field(default_factory=dict)
    resources: ExecutionProfileResources = Field(default_factory=ExecutionProfileResources)
    config: dict[str, object] = Field(default_factory=dict)
    notes: str | None = None


class ExecutionProfileSource(BaseModel):
    """Where a profile definition came from."""

    kind: Literal["built-in", "repo", "repo-local", "explicit", "recorded"]
    path: str | None = None


class ResolvedExecutionProfile(BaseModel):
    """Resolved execution profile plus source metadata."""

    name: str
    source: ExecutionProfileSource
    profile: ExecutionProfile


class RemovedExecutionProfile(BaseModel):
    """A built-in profile Metaproc no longer ships, kept so its runs can continue.

    ``definition`` is the profile as the last release that shipped it defined it, so a
    run that recorded the profile rebuilds the same plan and step fingerprints on resume
    (``recorded_execution_profiles``). ``successor`` is the built-in to move to.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    removed_on: date
    reason: str
    successor: str
    definition: ExecutionProfile


def _removed_profile(
    *, reason: str, successor: str, definition: dict[str, object]
) -> RemovedExecutionProfile:
    return RemovedExecutionProfile(
        removed_on=date(2026, 11, 18),
        reason=reason,
        successor=successor,
        definition=ExecutionProfile.model_validate(definition),
    )


_GEMINI_CLI_CONFIG = {"permission_mode": "bypassPermissions", "output_format": "stream-json"}
_GEMINI_CLI_RESOURCES = {"estimated_process_rss_mb": 250, "initial_memory_budget_fraction": 0.5}
_PI_RESOURCES = {"estimated_process_rss_mb": 500, "initial_memory_budget_fraction": 0.25}

REMOVED_EXECUTION_PROFILES: Mapping[str, RemovedExecutionProfile] = {
    "gemini-flash-36": _removed_profile(
        reason="Vertex AI retires gemini-3.6-flash on 2026-11-19",
        successor="gemini-hydrogen",
        definition={
            "status": "tested",
            "adapter": "gemini-cli",
            "provider": "google-vertex",
            "model": "gemini-3.6-flash",
            "resources": _GEMINI_CLI_RESOURCES,
            "config": {"model": "gemini-3.6-flash", **_GEMINI_CLI_CONFIG},
        },
    ),
    "gemini-flash": _removed_profile(
        reason="its model gemini-3.5-flash was dropped from the model catalog",
        successor="gemini-hydrogen",
        definition={
            "status": "experimental",
            "adapter": "gemini-cli",
            "provider": "google-vertex",
            "model": "gemini-3.5-flash",
            "resources": _GEMINI_CLI_RESOURCES,
            "config": {"model": "gemini-3.5-flash", **_GEMINI_CLI_CONFIG},
        },
    ),
    "pi-gemini-flash": _removed_profile(
        reason="its model gemini-3.5-flash was dropped from the model catalog",
        successor="pi-gemini-hydrogen",
        definition={
            "status": "experimental",
            "adapter": "pi-cli",
            "provider": "google-vertex",
            "model": "gemini-3.5-flash",
            "resources": _PI_RESOURCES,
            "config": {
                "provider": "google-vertex",
                "model": "gemini-3.5-flash",
                "no_session_persistence": True,
            },
        },
    ),
    "pi-glm5": _removed_profile(
        reason="Vertex AI retired its model glm-5-maas on 2026-10-21",
        successor="pi-glm52",
        definition={
            "status": "tested",
            "adapter": "pi-cli",
            "provider": "vertex-maas",
            "model": "glm-5-maas",
            "thinking": "high",
            "resources": _PI_RESOURCES,
            "config": {
                "provider": "vertex-maas",
                "model": "glm-5-maas",
                "no_session_persistence": True,
            },
        },
    ),
}
"""Built-in execution profiles removed from ``execution-profiles.default.yaml``.

A fresh launch that names one is refused with its successor (``removed_profile_note``).
A resume of a run whose ``run-config.yaml`` records one rebuilds it from ``definition``
and continues with a warning. The ``resolved_profiles`` snapshot a run records holds
the adapter and the first step's merged config but not the profile's resources, which
are part of every step fingerprint, so it alone cannot rebuild the same plan.
"""


def removed_profile_note(name: str) -> str | None:
    """Return why the built-in profile *name* was removed and its successor, if it was."""
    removed = REMOVED_EXECUTION_PROFILES.get(name)
    if removed is None:
        return None
    return (
        f"execution profile {name!r} was removed from Metaproc's built-in profiles on "
        f"{removed.removed_on.isoformat()} ({removed.reason}); its successor is "
        f"{removed.successor!r}"
    )


def recorded_profile_names(run_config: Mapping[object, object]) -> set[str]:
    """Return every execution-profile name a ``run-config.yaml`` mapping records.

    The run profile (``execution_profile``), the ``--step-variant`` values, and the
    names in the ``resolved_profiles`` snapshot.
    """
    names: set[str] = set()
    profile = run_config.get("execution_profile")
    if isinstance(profile, str) and profile:
        names.add(profile)
    step_variants = run_config.get("step_variants")
    if isinstance(step_variants, Mapping):
        values = cast("Mapping[object, object]", step_variants).values()
        names.update(value for value in values if isinstance(value, str) and value)
    snapshot = run_config.get("resolved_profiles")
    if isinstance(snapshot, list):
        for entry in cast("list[object]", snapshot):
            if isinstance(entry, Mapping):
                name = cast("Mapping[object, object]", entry).get("name")
                if isinstance(name, str) and name:
                    names.add(name)
    return names


def recorded_execution_profiles(run_config: Mapping[object, object]) -> dict[str, ExecutionProfile]:
    """Return the removed built-in profiles a run records, as it was created with them.

    A resume and ``metaproc status`` pass these to ``ExecutionProfileRegistry.load`` as
    its lowest-precedence layer: a name the registry still defines resolves as it does
    for any launch, and a removed one rebuilds from its former definition.
    """
    return {
        name: REMOVED_EXECUTION_PROFILES[name].definition
        for name in sorted(recorded_profile_names(run_config))
        if name in REMOVED_EXECUTION_PROFILES
    }


class _RegistryEntry(BaseModel):
    name: str
    source: ExecutionProfileSource
    profile: ExecutionProfile


class ExecutionProfileRegistry(BaseModel):
    """Registry of named execution profiles after source precedence is applied."""

    entries: dict[str, _RegistryEntry] = Field(default_factory=dict)

    @classmethod
    def load(
        cls,
        *,
        repo_root: Path | None = None,
        explicit_files: list[Path] | tuple[Path, ...] = (),
        include_builtins: bool = True,
        recorded_profiles: Mapping[str, ExecutionProfile] | None = None,
    ) -> ExecutionProfileRegistry:
        """Load profile sources in increasing precedence order.

        *recorded_profiles* are profiles a resumed run recorded that no source defines any
        longer (``recorded_execution_profiles``). They are the lowest layer, with source
        kind ``recorded``, so every file source overrides them.
        """
        entries: dict[str, _RegistryEntry] = {
            name: _RegistryEntry(
                name=name, source=ExecutionProfileSource(kind="recorded"), profile=profile
            )
            for name, profile in (recorded_profiles or {}).items()
        }
        if include_builtins:
            cls._load_builtin(entries)
        if repo_root is not None:
            repo_metaproc = repo_root / ".metaproc"
            cls._load_file(
                entries,
                repo_metaproc / "execution-profiles.yaml",
                source=ExecutionProfileSource(
                    kind="repo", path=str(repo_metaproc / "execution-profiles.yaml")
                ),
                missing_ok=True,
            )
            cls._load_file(
                entries,
                repo_metaproc / "execution-profiles.local.yaml",
                source=ExecutionProfileSource(
                    kind="repo-local",
                    path=str(repo_metaproc / "execution-profiles.local.yaml"),
                ),
                missing_ok=True,
            )
        for explicit_file in explicit_files:
            cls._load_file(
                entries,
                explicit_file,
                source=ExecutionProfileSource(kind="explicit", path=str(explicit_file)),
            )
        return cls(entries=entries)

    @classmethod
    def _load_builtin(cls, entries: dict[str, _RegistryEntry]) -> None:
        resource = files("metaproc.data").joinpath("execution-profiles.default.yaml")
        raw = resource.read_text()
        cls._load_text(
            entries,
            raw,
            source=ExecutionProfileSource(
                kind="built-in", path="metaproc.data/execution-profiles.default.yaml"
            ),
        )

    @classmethod
    def _load_file(
        cls,
        entries: dict[str, _RegistryEntry],
        path: Path,
        *,
        source: ExecutionProfileSource,
        missing_ok: bool = False,
    ) -> None:
        if not path.is_file():
            if missing_ok:
                return
            msg = f"profile registry file not found: {path}"
            raise ValueError(msg)
        cls._load_text(entries, path.read_text(encoding="utf-8"), source=source)

    @classmethod
    def _load_text(
        cls,
        entries: dict[str, _RegistryEntry],
        text: str,
        *,
        source: ExecutionProfileSource,
    ) -> None:
        raw = from_yaml_string(text) or {}
        if not isinstance(raw, dict):
            msg = f"{_format_source(source)}: profile registry must be a mapping"
            raise ValueError(msg)
        profiles = raw.get("profiles", {})
        if not isinstance(profiles, dict):
            msg = f"{_format_source(source)}: profiles must be a mapping"
            raise ValueError(msg)
        for name, raw_profile in profiles.items():
            if not isinstance(name, str) or not name.strip():
                msg = f"{_format_source(source)}: profile names must be non-empty strings"
                raise ValueError(msg)
            if not isinstance(raw_profile, dict):
                msg = f"{_format_source(source)}: profile {name!r} must be a mapping"
                raise ValueError(msg)
            _reject_secret_shapes(raw_profile, path=f"profile {name!r}")
            profile = ExecutionProfile.model_validate(raw_profile)
            entries[name] = _RegistryEntry(name=name, source=source, profile=profile)

    @property
    def names(self) -> list[str]:
        return sorted(self.entries)

    def resolve(
        self,
        name: str,
        *,
        config_overrides: dict[str, object] | None = None,
    ) -> ResolvedExecutionProfile:
        """Resolve a profile name, applying parent inheritance and config overrides."""
        entry = self.entries.get(name)
        if entry is None:
            available = ", ".join(self.names) or "<none>"
            msg = f"unknown execution profile: {name!r} (available: {available})"
            if note := removed_profile_note(name):
                msg += f"; {note}"
            raise ValueError(msg)
        resolved = self._resolve_entry(entry, stack=[])
        if config_overrides:
            profile = resolved.profile.model_copy(
                deep=True,
                update={"config": {**resolved.profile.config, **config_overrides}},
            )
            resolved = resolved.model_copy(update={"profile": profile})
        if not resolved.profile.adapter:
            msg = f"execution profile {name!r} does not resolve an adapter"
            raise ValueError(msg)
        return resolved

    def list_resolved(self) -> list[ResolvedExecutionProfile]:
        """Resolve every profile in name order."""
        return [self.resolve(name) for name in self.names]

    def _resolve_entry(
        self,
        entry: _RegistryEntry,
        *,
        stack: list[str],
    ) -> ResolvedExecutionProfile:
        if entry.name in stack:
            chain = " -> ".join([*stack, entry.name])
            msg = f"execution profile extends cycle: {chain}"
            raise ValueError(msg)
        parent_name = entry.profile.extends
        if not parent_name:
            return ResolvedExecutionProfile(
                name=entry.name,
                source=entry.source,
                profile=entry.profile,
            )
        parent_entry = self.entries.get(parent_name)
        if parent_entry is None:
            msg = f"execution profile {entry.name!r} extends unknown profile {parent_name!r}"
            raise ValueError(msg)
        parent = self._resolve_entry(parent_entry, stack=[*stack, entry.name])
        merged = _merge_profiles(parent.profile, entry.profile)
        return ResolvedExecutionProfile(name=entry.name, source=entry.source, profile=merged)


def _merge_profiles(parent: ExecutionProfile, child: ExecutionProfile) -> ExecutionProfile:
    data = parent.model_dump(by_alias=True, exclude_none=True)
    child_data = child.model_dump(by_alias=True, exclude_none=True)

    for field_name, field_info in type(child).model_fields.items():
        if field_name in {"capabilities", "resources", "config", "schema_"}:
            continue
        if field_name in child.model_fields_set:
            alias = field_info.alias or field_name
            if alias in child_data:
                data[alias] = child_data[alias]

    if "capabilities" in child.model_fields_set:
        data["capabilities"] = {**parent.capabilities, **child.capabilities}
    if "config" in child.model_fields_set:
        data["config"] = {**parent.config, **child.config}
    if "resources" in child.model_fields_set:
        parent_resources = parent.resources.model_dump(exclude_none=True)
        child_resources = child.resources.model_dump(exclude_none=True)
        data["resources"] = {**parent_resources, **child_resources}

    data.pop("extends", None)
    return ExecutionProfile.model_validate(data)


def _reject_secret_shapes(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() in _SECRET_KEY_NAMES:
                msg = f"{path}: secret-shaped key {key_text!r} is not allowed in execution profiles"
                raise ValueError(msg)
            _reject_secret_shapes(child, path=f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_shapes(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _looks_secret_value(value):
        msg = f"{path}: secret-shaped value is not allowed in execution profiles"
        raise ValueError(msg)


def _looks_secret_value(value: str) -> bool:
    stripped = value.strip()
    if "BEGIN PRIVATE KEY" in stripped:
        return True
    return stripped.startswith(_SECRET_VALUE_PREFIXES)


def _format_source(source: ExecutionProfileSource) -> str:
    return source.path or source.kind
