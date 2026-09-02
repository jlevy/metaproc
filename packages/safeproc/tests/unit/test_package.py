"""Package contracts: version, CLI entry, import boundary, and zero runtime dependencies."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import requires

import pytest

import safeproc
import safeproc._platform.linux as linux_provider
from safeproc.cli import build_parser, main


def test_version_is_a_string() -> None:
    assert isinstance(safeproc.__version__, str) and safeproc.__version__


def test_cli_version_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert safeproc.__version__ in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["watch", "--help"])
    assert exc.value.code == 0
    assert "--policy" in capsys.readouterr().out


def test_parser_defaults_are_observation() -> None:
    args = build_parser().parse_args(["watch", "--pid", "1"])
    assert args.policy == "observe"
    assert args.dry_run is False


def test_importing_safeproc_never_loads_metaproc() -> None:
    code = (
        "import sys, safeproc, safeproc.cli, safeproc.monitor, safeproc.replay; "
        "bad = sorted(m for m in sys.modules if m == 'metaproc' or m.startswith('metaproc.')); "
        "assert not bad, bad; print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_runtime_dependencies_are_empty() -> None:
    declared = requires("safeproc") or []
    runtime = [entry for entry in declared if "extra ==" not in entry]
    assert runtime == [], runtime


def test_hot_path_modules_do_not_import_subprocess_on_linux() -> None:
    """The Linux sampling path forks nothing. Only the Darwin fallbacks use subprocess."""
    assert not hasattr(linux_provider, "subprocess")
