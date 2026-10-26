"""Safe YAML writers stay on ruamel's pure-Python emitter.

When ruamel's optional C extension (``_ruamel_yaml``) is importable, every
``YAML(typ="safe")`` handle switches its emitter to libyaml's ``CEmitter``. That
emitter wraps long plain scalars at different points than the pure ``Emitter``, and it
does not flush the output stream at the end of a dump, so a dump to a handle that is
never closed or flushed leaves an empty file. Metaproc's safe writers therefore pin the
pure emitter, and their bytes must not depend on whether the extension is installed.

The comparison tests need the extension and skip without it in the ordinary suite.
``make test-yaml-c-emitter`` requires the native implementation and fails if it is
unavailable. The reference output is produced by the same writer with the extension
hidden from ruamel, which is exactly what a process without the extension writes.
"""

from __future__ import annotations

import ast
import importlib
import io
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
import ruamel.yaml.emitter
import ruamel.yaml.main
from frontmatter_format import new_yaml
from ruamel.yaml import YAML

from metaproc.io import new_dump_yaml, read_yaml_file
from metaproc.osutils.memory_pressure import PressureLevel
from metaproc.runpool import kill
from metaproc.runpool.status import (
    ControllerStatus,
    PressureStatus,
    ProcessStatus,
    RunPoolStatus,
    ScaleOverride,
    ScaleState,
    write_scale_override,
    write_scale_state,
    write_status,
)


def _c_extension_import_error() -> str | None:
    try:
        importlib.import_module("_ruamel_yaml")
    except ImportError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


_C_IMPORT_ERROR = _c_extension_import_error()

requires_c_emitter = pytest.mark.skipif(
    _C_IMPORT_ERROR is not None,
    reason=(
        "ruamel.yaml's C extension (_ruamel_yaml) is not importable "
        f"({_C_IMPORT_ERROR}); without it every safe handle already uses the pure emitter"
    ),
)

# ``CEmitter`` is ``None`` in ``ruamel.yaml.main`` when the extension is absent; the
# module does not export it, so reach it untyped.
_RUAMEL_MAIN: Any = ruamel.yaml.main

if os.environ.get("METAPROC_REQUIRE_C_EMITTER") == "1" and (
    _C_IMPORT_ERROR is not None or _RUAMEL_MAIN.CEmitter is None
):
    raise RuntimeError(
        "The native YAML emitter lane requires an importable _ruamel_yaml CEmitter: "
        f"{_C_IMPORT_ERROR or 'ruamel.yaml.main.CEmitter is unavailable'}"
    )

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "metaproc"

# Long plain scalars with spaces: the C and pure emitters choose different break
# points for these, so they expose an emitter switch in the written bytes.
LONG_LABEL = (
    "extract-quarterly-signals-for-a-long-item-identifier " + "x" * 30 + " " + "y" * 60 + " z"
)
LONG_REASON = (
    "operator requested drain because the pool exceeded its memory budget during "
    "the nightly refresh window"
)


@contextmanager
def _pure_emitter_only() -> Generator[None]:
    """Hide the C emitter from ruamel, as in a process without the extension."""
    saved = _RUAMEL_MAIN.CEmitter
    _RUAMEL_MAIN.CEmitter = None
    try:
        yield
    finally:
        _RUAMEL_MAIN.CEmitter = saved


def _sample_status() -> RunPoolStatus:
    return RunPoolStatus(
        pool_id="pool-emitter-check",
        pid=os.getpid(),
        started_at="2026-04-03T22:15:00",
        updated_at="2026-04-03T22:17:30",
        backend="local",
        max_concurrency=20,
        current_concurrency=12,
        active_count=1,
        pending_count=5,
        completed_count=10,
        failed_count=1,
        killed_count=1,
        pressure=PressureStatus(
            level=PressureLevel.NORMAL,
            available_pct=55.0,
            swap_used_gb=1.2,
            total_memory_gb=32.0,
            source="macos-memorystatus",
        ),
        controller=ControllerStatus(
            mode="adaptive",
            operator_cap=20,
            effective_target=12,
            memory_ceiling=20,
            provider_ceiling=12,
            bottleneck="DSQ-bound",
            recent_rate_limits=1,
            pending_retries=2,
        ),
        processes=[
            ProcessStatus(
                pid=12345,
                backend="local",
                label=LONG_LABEL,
                started_at="2026-04-03T22:15:05",
                elapsed_s=145.3,
                status="running",
            ),
        ],
        recent_completions=[
            ProcessStatus(
                pid=12340,
                backend="local",
                label="done-process",
                started_at="2026-04-03T22:14:00",
                elapsed_s=98.7,
                status="killed",
                exit_code=-9,
                kill_reason=LONG_REASON,
            ),
        ],
    )


def _sample_scale_state() -> ScaleState:
    return ScaleState(
        updated_at="2026-04-03T22:17:30",
        controller=ControllerStatus(
            mode="adaptive",
            operator_cap=20,
            effective_target=12,
            memory_ceiling=20,
            provider_ceiling=12,
            bottleneck=LONG_REASON,
        ),
        desired_workers=None,
        generation=3,
    )


def _write_and_read(write: Callable[[Path], object], path: Path) -> bytes:
    write(path)
    return path.read_bytes()


def _assert_same_bytes_as_pure(write: Callable[[Path], object], tmp_path: Path) -> None:
    with_c = _write_and_read(write, tmp_path / "with-c.yaml")
    with _pure_emitter_only():
        pure = _write_and_read(write, tmp_path / "pure.yaml")
    assert with_c.decode("utf-8") == pure.decode("utf-8")
    _assert_fixture_discriminates(tmp_path / "pure.yaml")


def _assert_fixture_discriminates(pure_path: Path) -> None:
    """The written values, dumped through the actual C emitter, must differ.

    Without this the comparison could pass on a fixture whose scalars both emitters
    happen to break identically, and an emitter switch would go unnoticed.
    """
    values = read_yaml_file(pure_path)
    unpinned = io.StringIO()
    native = new_yaml(suppress_vals=lambda _value: False)
    # frontmatter-format keeps its own factory pure; the negative control must
    # explicitly select C without changing its representers or suppression policy.
    native.Emitter = _RUAMEL_MAIN.CEmitter
    native.dump(values, unpinned)
    assert unpinned.getvalue() != pure_path.read_text(encoding="utf-8")


@requires_c_emitter
def test_environment_selects_the_c_emitter_for_plain_safe_handles() -> None:
    assert _RUAMEL_MAIN.CEmitter is not None
    assert YAML(typ="safe").Emitter is _RUAMEL_MAIN.CEmitter


@requires_c_emitter
def test_write_status_bytes_match_the_pure_emitter(tmp_path: Path) -> None:
    status = _sample_status()
    _assert_same_bytes_as_pure(lambda path: write_status(path, status), tmp_path)


@requires_c_emitter
def test_write_scale_state_bytes_match_the_pure_emitter(tmp_path: Path) -> None:
    state = _sample_scale_state()
    _assert_same_bytes_as_pure(lambda path: write_scale_state(path, state), tmp_path)


@requires_c_emitter
def test_write_scale_override_bytes_match_the_pure_emitter(tmp_path: Path) -> None:
    override = ScaleOverride(mode=LONG_REASON, operator_cap=8)
    _assert_same_bytes_as_pure(lambda path: write_scale_override(path, override), tmp_path)


@requires_c_emitter
def test_kill_sentinel_bytes_match_the_pure_emitter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = datetime(2026, 4, 3, 22, 17, 30, tzinfo=UTC)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _FrozenDatetime:
            return cls.fromtimestamp(frozen.timestamp(), tz=tz)

    monkeypatch.setattr(kill, "datetime", _FrozenDatetime)

    def write(path: Path) -> Path:
        path.mkdir()
        return kill._write_sentinel(  # pyright: ignore[reportPrivateUsage]
            path, reason=LONG_REASON, sig="SIGTERM", variant=LONG_LABEL, drain=True
        )

    c_path = write(tmp_path / "with-c")
    with _pure_emitter_only():
        pure_path = write(tmp_path / "pure")
    assert c_path.read_bytes() == pure_path.read_bytes()
    _assert_fixture_discriminates(pure_path)
    assert read_yaml_file(c_path)["variant"] == LONG_LABEL


@requires_c_emitter
def test_dump_yaml_handle_uses_the_pure_emitter() -> None:
    yaml = new_dump_yaml(suppress_vals=lambda _value: False)
    assert yaml.Emitter is ruamel.yaml.emitter.Emitter


def test_dump_yaml_preserves_public_factory_suppression_behavior() -> None:
    data = {"null": None, "empty": {}, "zero": 0, "text": "keep", "drop": "omit"}
    default_output = io.StringIO()
    new_dump_yaml().dump(data, default_output)
    assert YAML(typ="safe", pure=True).load(default_output.getvalue()) == {
        "zero": 0,
        "text": "keep",
        "drop": "omit",
    }

    for suppress in (None, lambda value: value == "omit"):
        actual = io.StringIO()
        expected = io.StringIO()
        new_dump_yaml(suppress_vals=suppress).dump(data, actual)
        with _pure_emitter_only():
            new_yaml(suppress_vals=suppress).dump(data, expected)
        assert actual.getvalue() == expected.getvalue()


@requires_c_emitter
def test_dump_to_an_unclosed_handle_is_complete(tmp_path: Path) -> None:
    data = {"reason": LONG_REASON, "variant": None, "drain": True}
    with _pure_emitter_only():
        expected = _dump_to_open_handle(data, tmp_path / "pure.yaml")
    assert expected, "the pure emitter flushes the stream at the end of a dump"
    assert _dump_to_open_handle(data, tmp_path / "with-c.yaml") == expected


def _dump_to_open_handle(data: dict[str, Any], path: Path) -> str:
    handle = path.open("w", encoding="utf-8")
    try:
        new_dump_yaml(suppress_vals=lambda _value: False).dump(data, handle)
        # Read through a separate handle before this one is flushed or closed.
        return path.read_text(encoding="utf-8")
    finally:
        handle.close()


# ── Source guard (runs with or without the extension) ───────────────────

# Production YAML handles that only ever parse. They may stay on the default (C when
# available) parser; the guard rejects any other non-round-trip handle.
LOAD_ONLY_YAML_SITES = {("engine/yaml_repair.py", "_ruamel_safe_load")}
DUMP_FACTORY_MODULE = "io/yaml_dump.py"


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _is_round_trip(call: ast.Call) -> bool:
    typ = _keyword(call, "typ")
    return isinstance(typ, ast.Constant) and typ.value in ("rt", "rtsc")


def _is_pure(call: ast.Call) -> bool:
    pure = _keyword(call, "pure")
    return isinstance(pure, ast.Constant) and pure.value is True


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _enclosing_functions(tree: ast.AST) -> dict[ast.AST, str]:
    owners: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                owners.setdefault(child, node.name)
    return owners


def test_no_source_builds_a_safe_yaml_handle_outside_the_dump_factory() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owners = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (rel, owners.get(node, "")) in LOAD_ONLY_YAML_SITES:
                continue
            name = _callee_name(node)
            if name == "new_yaml" and rel != DUMP_FACTORY_MODULE and not _is_round_trip(node):
                offenders.append(f"{rel}:{node.lineno} new_yaml() without typ='rt'")
            if name == "YAML" and not (_is_round_trip(node) or _is_pure(node)):
                offenders.append(f"{rel}:{node.lineno} YAML() without typ='rt' or pure=True")
    assert offenders == [], (
        "Build dumping handles with metaproc.io.new_dump_yaml so the pure emitter is kept "
        "when ruamel's C extension is installed. For parsing-only handles, record the "
        "site in LOAD_ONLY_YAML_SITES: " + ", ".join(offenders)
    )


def test_source_guard_accepts_both_load_only_constructors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "engine" / "yaml_repair.py"
    path.parent.mkdir()
    path.write_text(
        dedent("""\
            def _ruamel_safe_load(text):
                YAML(typ="safe").load(text)
                new_yaml().load(text)
            """),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "SRC_ROOT", tmp_path)
    test_no_source_builds_a_safe_yaml_handle_outside_the_dump_factory()


def test_source_guard_rejects_both_unapproved_dump_constructors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "writer.py"
    path.write_text(
        dedent("""\
            def write(data, stream):
                YAML(typ="safe").dump(data, stream)
                new_yaml().dump(data, stream)
            """),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "SRC_ROOT", tmp_path)
    with pytest.raises(AssertionError, match=r"YAML\(\).*new_yaml\(\)"):
        test_no_source_builds_a_safe_yaml_handle_outside_the_dump_factory()
