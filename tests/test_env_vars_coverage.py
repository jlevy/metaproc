"""Enforce that every env-var read goes through a typed registry.

This is the Phase 2 merge gate for
``docs/arch/arch-metaproc-core.md``.

Rule
----
Every ``os.environ.get(<LITERAL>, ...)`` / ``os.getenv(<LITERAL>, ...)`` call
and every ``os.environ[<LITERAL>]`` read in ``src/metaproc/`` must use a
name that is **not** a member of :class:`MetaprocEnv` — because registered
names must go through the typed accessor (for example,
``MetaprocEnv.METAPROC_GCP_PROJECT.read_str()``).

An explicit allowlist covers legitimate dynamic-name lookups and writes.
Each entry names the file + line and a one-line reason so the gate's
blind spots are readable, not hidden.
"""

from __future__ import annotations

import ast
from pathlib import Path

from metaproc.config.env_vars import MetaprocEnv

REPO_ROOT = Path(__file__).resolve().parents[1]
METAPROC_SRC = REPO_ROOT / "src" / "metaproc"

REGISTERED_NAMES: frozenset[str] = frozenset(member.name for member in MetaprocEnv)

# Keys are paths relative to the repo root (including the ``src/`` layer)
# and a 1-indexed line number; values are the reason the exemption exists.
# Allowlist entries cover legitimate dynamic-name reads and the registry
# accessor itself. Everything else must migrate to MetaprocEnv.<NAME>.read_*().
ALLOWLIST: dict[tuple[str, int], str] = {
    # Registry accessor internals — reads the member name dynamically.
    ("src/metaproc/config/env_enum.py", 124): "registry accessor _raw_value()",
    # SecretRef.resolve reads env vars by name from the SecretRef instance:
    # the dataclass holds registered names but the read is through a local
    # Mapping captured at runtime.
    ("src/metaproc/dispatch/secret_refs.py", 55): (
        "SecretRef.resolve: dynamic env via Mapping default to os.environ"
    ),
    # _build_env_exports iterates a literal list of var names.
    ("src/metaproc/commands/gcp.py", 2288): "iterates _REMOTE_RUN_ENV_VARS literal list",
    # Template-placeholder fallback: the key is an arbitrary template
    # variable name, not a fixed env var, so the call is genuinely dynamic.
    ("src/metaproc/engine/placeholders.py", 84): "template {{key}} dynamic env fallback",
}


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _extract_literal_name(arg: ast.expr) -> str | None:
    """Return the string literal if ``arg`` is one, else ``None``."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _is_os_environ_call(node: ast.Call) -> bool:
    """Match ``os.environ.get(...)`` and ``os.getenv(...)``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        # os.environ.get — Attribute(value=Attribute(value=Name("os"), attr="environ"), attr="get")
        if func.attr == "get" and isinstance(func.value, ast.Attribute):
            inner = func.value
            if (
                inner.attr == "environ"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "os"
            ):
                return True
        # os.getenv — Attribute(value=Name("os"), attr="getenv")
        if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
            return True
    return False


def _find_violations(src_root: Path) -> list[tuple[str, int, str, str]]:
    """Return ``(rel_path, line, var_name, reason)`` for every unsafe env read.

    Paths in the result are repo-root-anchored posix strings — the same
    form the allowlist keys take.
    """
    violations: list[tuple[str, int, str, str]] = []
    for py_file in _python_files(src_root):
        lookup_key_base = py_file.relative_to(REPO_ROOT).as_posix()

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            # Call forms: os.environ.get(NAME, ...) / os.getenv(NAME, ...)
            if isinstance(node, ast.Call) and _is_os_environ_call(node):
                if not node.args:
                    continue
                name = _extract_literal_name(node.args[0])
                key = (lookup_key_base, node.lineno)
                if name is None:
                    # Dynamic lookup — must be allowlisted.
                    if key not in ALLOWLIST:
                        violations.append(
                            (
                                lookup_key_base,
                                node.lineno,
                                "<dynamic>",
                                "dynamic env-var name requires allowlist entry",
                            )
                        )
                    continue
                if name in REGISTERED_NAMES:
                    if key in ALLOWLIST:
                        continue
                    violations.append(
                        (
                            lookup_key_base,
                            node.lineno,
                            name,
                            f"use <Registry>.{name}.read_*() instead of os.environ.get/getenv",
                        )
                    )

            # Subscript read form: os.environ["NAME"] — excludes writes (ast.Assign target).
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"
                and isinstance(node.ctx, ast.Load)
            ):
                key = (lookup_key_base, node.lineno)
                idx = node.slice
                name = _extract_literal_name(idx)
                if name is not None and name in REGISTERED_NAMES and key not in ALLOWLIST:
                    violations.append(
                        (
                            lookup_key_base,
                            node.lineno,
                            name,
                            f"use <Registry>.{name}.read_*() instead of os.environ[...]",
                        )
                    )

    return violations


def test_metaproc_src_routes_env_reads_through_registry() -> None:
    violations = _find_violations(METAPROC_SRC)
    if violations:
        lines = [f"  {path}:{line} — {name}: {reason}" for path, line, name, reason in violations]
        msg = (
            f"{len(violations)} env-var read(s) bypass the typed registry:\n"
            + "\n".join(lines)
            + "\n\nEither migrate to MetaprocEnv.<NAME>.read_*() / "
            + "add a narrow allowlist entry "
            + "(with reason) to test_env_vars_coverage.py."
        )
        raise AssertionError(msg)


def test_allowlist_entries_are_current() -> None:
    """Fail if an allowlist entry points at a line that no longer needs it.

    Prevents rot: once a call site is migrated or removed, its allowlist
    entry must come out too.
    """
    stale: list[tuple[str, int]] = []
    for rel_path, line in ALLOWLIST:
        abs_path = REPO_ROOT / rel_path
        if not abs_path.is_file():
            stale.append((rel_path, line))
            continue
        try:
            content = abs_path.read_text().splitlines()
        except OSError:
            stale.append((rel_path, line))
            continue
        if line < 1 or line > len(content):
            stale.append((rel_path, line))
            continue
        raw = content[line - 1]
        # Allow the line to contain either an os.environ read, an os.environ
        # write (for the two write-side entries), or an os.environ.items()
        # / **os.environ spread.
        if "os.environ" not in raw and "os.getenv" not in raw:
            stale.append((rel_path, line))
    if stale:
        lines = [f"  {path}:{line}" for path, line in stale]
        raise AssertionError(
            "Stale allowlist entries (file gone, line out of range, or line "
            "no longer touches os.environ/os.getenv):\n" + "\n".join(lines)
        )
