"""Check cross-file supply-chain invariants not covered by standard tools."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NPMRC_SETTINGS = {
    "engine-strict=true",
    "ignore-scripts=true",
    "min-release-age=14",
    "package-lock=true",
    "save-exact=true",
}
NPM_ENGINE_RANGE = ">=11.10.0 <12"
DIRECT_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies")
EXACT_NPM_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
DOCKER_DIGEST = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-fA-F]{64}$")
NPM_REGISTRY_PREFIX = "https://registry.npmjs.org/"
SHA512_PREFIX = "sha512-"
UV_COOL_OFF = "14 days"
TOOLCHAIN_SCRIPT = "devtools/ensure-toolchain.sh"
AGENT_HOOK_CONFIGS = (".claude/settings.json", ".codex/hooks.json")


def _read_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return cast(dict[str, object], value)


def _string_map(data: dict[str, object], field: str) -> dict[str, str]:
    return cast(dict[str, str], data.get(field, {}))


def _check_npm(root: Path, errors: list[str]) -> None:
    npmrc = set((root / ".npmrc").read_text(encoding="utf-8").splitlines())
    missing = sorted(REQUIRED_NPMRC_SETTINGS - npmrc)
    if missing:
        errors.append(f".npmrc is missing required settings: {missing}")

    package = _read_json(root / "package.json")
    if package.get("private") is not True:
        errors.append("package.json must set private to true")
    engines = _string_map(package, "engines")
    if not engines.get("node"):
        errors.append("package.json must declare a nonempty node engine")
    if engines.get("npm") != NPM_ENGINE_RANGE:
        errors.append(f"package.json must require npm {NPM_ENGINE_RANGE}")
    pins: list[str] = []
    for name in (".nvmrc", ".node-version"):
        path = root / name
        if not path.is_file():
            errors.append(f"{name} must exist")
            continue
        pins.append(path.read_text(encoding="utf-8").strip())
    if len(pins) == 2 and (not all(pins) or len(set(pins)) != 1):
        errors.append(".nvmrc and .node-version must match and be nonempty")

    for field in DIRECT_DEPENDENCY_FIELDS:
        dependencies = _string_map(package, field)
        for name, spec in dependencies.items():
            if EXACT_NPM_VERSION.fullmatch(spec) is None:
                errors.append(f"package.json {field}.{name} must use an exact version")

    lock = _read_json(root / "package-lock.json")
    packages_value = lock.get("packages")
    if not isinstance(packages_value, dict):
        errors.append("package-lock.json must contain a packages object")
        return
    packages = cast(dict[object, object], packages_value)
    for package_path, entry_value in packages.items():
        if package_path == "":
            continue
        if not isinstance(package_path, str) or not isinstance(entry_value, dict):
            errors.append("package-lock.json package entries must be objects")
            continue
        entry = cast(dict[str, object], entry_value)
        if not str(entry.get("resolved", "")).startswith(NPM_REGISTRY_PREFIX):
            errors.append(
                f"package-lock.json {package_path} must resolve from {NPM_REGISTRY_PREFIX}"
            )
        if not str(entry.get("integrity", "")).startswith(SHA512_PREFIX):
            errors.append(f"package-lock.json {package_path} must have sha512 integrity")


def _check_uv(root: Path, errors: list[str]) -> None:
    config = tomllib.loads((root / "uv.toml").read_text(encoding="utf-8"))
    if config.get("exclude-newer") != UV_COOL_OFF:
        errors.append(f"uv.toml must set exclude-newer to {UV_COOL_OFF}")


def _check_toolchain_bootstrap(root: Path, errors: list[str]) -> None:
    """Keep the session bootstrap pinned to the same toolchain as the repository.

    The bootstrap carries its own version constants because it also carries the
    matching download checksums. Asserting them against the canonical pins turns a
    half-finished version bump into a failed gate instead of an agent session that
    silently installs the wrong toolchain.
    """
    script = root / TOOLCHAIN_SCRIPT
    if not script.is_file():
        errors.append(f"{TOOLCHAIN_SCRIPT} must exist")
        return
    text = script.read_text(encoding="utf-8")

    node_pin = (root / ".node-version").read_text(encoding="utf-8").strip()
    declared_node = re.search(r'^NODE_VERSION="([^"]+)"', text, re.MULTILINE)
    if declared_node is None:
        errors.append(f"{TOOLCHAIN_SCRIPT} must declare NODE_VERSION")
    elif declared_node.group(1) != node_pin:
        errors.append(
            f"{TOOLCHAIN_SCRIPT} NODE_VERSION {declared_node.group(1)} must match "
            f".node-version {node_pin} (update the pinned checksums too)"
        )

    uv_config = tomllib.loads((root / "uv.toml").read_text(encoding="utf-8"))
    uv_floor = str(uv_config.get("required-version", "")).lstrip("><=~^ ")
    declared_uv = re.search(r'^UV_VERSION="([^"]+)"', text, re.MULTILINE)
    if declared_uv is None:
        errors.append(f"{TOOLCHAIN_SCRIPT} must declare UV_VERSION")
    elif uv_floor and declared_uv.group(1) != uv_floor:
        errors.append(
            f"{TOOLCHAIN_SCRIPT} UV_VERSION {declared_uv.group(1)} must match the "
            f"uv.toml required-version floor {uv_floor} (update the pinned checksums too)"
        )

    # Every supported agent must run the same bootstrap first, or one agent's
    # sessions start without a toolchain. Order is part of the contract: the tbd
    # hook that follows needs the npx this bootstrap installs, so a regeneration
    # that reinstalls the agent hooks ahead of it has to fail the gate.
    for config_path in AGENT_HOOK_CONFIGS:
        path = root / config_path
        if not path.is_file():
            errors.append(f"{config_path} must exist")
            continue
        commands = _session_start_commands(path)
        if not any(TOOLCHAIN_SCRIPT in command for command in commands):
            errors.append(f"{config_path} must run {TOOLCHAIN_SCRIPT} on SessionStart")
        elif TOOLCHAIN_SCRIPT not in commands[0]:
            errors.append(
                f"{config_path} must run {TOOLCHAIN_SCRIPT} as its first SessionStart "
                f"hook; the hooks that follow need the toolchain it installs"
            )


def _session_start_commands(path: Path) -> list[str]:
    """The SessionStart hook commands of an agent config, in the order they run."""
    matchers = _read_json(path).get("hooks", {})
    if not isinstance(matchers, dict):
        return []
    commands: list[str] = []
    for matcher in matchers.get("SessionStart", []) or []:
        if not isinstance(matcher, dict):
            continue
        for hook in matcher.get("hooks", []) or []:
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                commands.append(hook["command"])
    return commands


def _check_workflows(root: Path, errors: list[str]) -> None:
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        uses = re.findall(r"^\s*-?\s*uses:\s+([^\s#]+)", text, re.MULTILINE)
        mutable: list[str] = []
        for use in uses:
            if use.startswith("./"):
                continue
            if use.startswith("docker://"):
                if DOCKER_DIGEST.fullmatch(use) is None:
                    mutable.append(use)
                continue
            if "@" not in use or FULL_SHA.fullmatch(use.rsplit("@", 1)[1]) is None:
                mutable.append(use)
        if mutable:
            errors.append(f"{path.name}: GitHub Actions must use full commit SHAs: {mutable}")
        if path.name not in {"publish.yml", "publish.yaml"}:
            continue
        if "publish --trusted-publishing" not in text:
            errors.append(f"{path.name}: publish workflow requires uv trusted publishing")
        if re.search(r"^\s*workflow_dispatch\s*:", text, re.MULTILINE) is not None:
            errors.append(f"{path.name}: publish workflow must not allow workflow_dispatch")
        if re.search(r"types:\s*\[\s*published\s*\]", text) is None:
            errors.append(f"{path.name}: trusted publishing must run only for published releases")
        if re.search(r"^\s*id-token:\s*write\s*(?:#.*)?$", text, re.MULTILINE) is None:
            errors.append(f"{path.name}: trusted publishing requires id-token: write")
        if re.search(r"^\s*environment:\s*\S+", text, re.MULTILINE) is None:
            errors.append(f"{path.name}: trusted publishing requires an environment")


def verify_supply_chain(root: Path = ROOT) -> None:
    """Raise with all repository supply-chain policy violations."""
    errors: list[str] = []
    _check_npm(root, errors)
    _check_uv(root, errors)
    _check_toolchain_bootstrap(root, errors)
    _check_workflows(root, errors)
    if errors:
        raise RuntimeError("Supply-chain policy violations:\n- " + "\n- ".join(errors))


def main() -> int:
    verify_supply_chain()
    print("Supply-chain checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
