# pyright: reportUnusedImport=false
"""metaproc CLI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from metaproc.errors import CLIError
from metaproc.output import OutputFormat, OutputManager

app = typer.Typer(
    name="metaproc",
    help="Generic process framework for structured multi-step agent workflows.",
    no_args_is_help=True,
)

# Global state set by callback, used by commands.
_output: OutputManager | None = None


def get_output() -> OutputManager:
    """Return the global OutputManager (created by the CLI callback)."""
    if _output is None:
        return OutputManager()
    return _output


@app.callback()
def main_callback(
    format: OutputFormat | None = typer.Option(  # noqa: UP007
        None, "--format", help="Output format (text, yaml, or json)."
    ),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress output."),
) -> None:
    """Global options applied before any subcommand."""
    global _output  # noqa: PLW0603
    _output = OutputManager(
        fmt=format or OutputFormat.TEXT,
        no_progress=no_progress,
    )
    # Load .env from cwd or ancestor (ensures adapter auth checks see credentials)
    _load_dotenv()

    # Deferred so tests can monkeypatch the module attribute (compare-matrix
    # plugin-defaults test re-binds this function at call time).
    from metaproc.plugins.discovery import (  # noqa: PLC0415 -- test monkeypatch boundary
        discover_and_load_plugins,
    )

    discover_and_load_plugins()


def _load_dotenv() -> None:
    """Walk up from cwd to find the nearest .env file and inject KEY=VALUE lines.

    Tolerates a leading ``export `` prefix on each line — the canonical
    ``.env.example`` template emits ``export VAR=value`` so a copy lands
    parseable by ``set -a; source .env`` in shell, and the loader must
    accept the same shape it generates.
    """
    p = Path.cwd()
    while True:
        candidate = p / ".env"
        if candidate.is_file():
            for raw in candidate.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and "=" in line:
                    os.environ.setdefault(key, value)
            break
        parent = p.parent
        if parent == p:
            break
        p = parent


# Import command modules to register them with the app.
import metaproc.commands.auth  # noqa: E402, F401
import metaproc.commands.auth_check  # noqa: E402, F401
import metaproc.commands.check_handlers  # noqa: E402, F401
import metaproc.commands.check_headers  # noqa: E402, F401
import metaproc.commands.claude_auth  # noqa: E402, F401
import metaproc.commands.codex_auth  # noqa: E402, F401
import metaproc.commands.compact_logs  # noqa: E402, F401
import metaproc.commands.compare  # noqa: E402, F401
import metaproc.commands.compare_matrix  # noqa: E402, F401
import metaproc.commands.compare_trace  # noqa: E402, F401
import metaproc.commands.deps  # noqa: E402, F401
import metaproc.commands.env  # noqa: E402, F401
import metaproc.commands.gcp  # noqa: E402, F401
import metaproc.commands.gzip_text  # noqa: E402, F401
import metaproc.commands.help  # noqa: E402, F401
import metaproc.commands.kill  # noqa: E402, F401
import metaproc.commands.liveness_watch  # noqa: E402, F401
import metaproc.commands.override  # noqa: E402, F401
import metaproc.commands.plan  # noqa: E402, F401
import metaproc.commands.pool  # noqa: E402, F401
import metaproc.commands.probe_tool_use  # noqa: E402, F401
import metaproc.commands.pulse  # noqa: E402, F401
import metaproc.commands.resource_report  # noqa: E402, F401
import metaproc.commands.resources  # noqa: E402, F401
import metaproc.commands.resume_daemon  # noqa: E402, F401
import metaproc.commands.run_manifest  # noqa: E402, F401
import metaproc.commands.run_parallel  # noqa: E402, F401
import metaproc.commands.run_process  # noqa: E402, F401
import metaproc.commands.run_step  # noqa: E402, F401
import metaproc.commands.searches  # noqa: E402, F401
import metaproc.commands.skill  # noqa: E402, F401
import metaproc.commands.softschema  # noqa: E402, F401
import metaproc.commands.stats  # noqa: E402, F401
import metaproc.commands.status  # noqa: E402, F401
import metaproc.commands.tail  # noqa: E402, F401
import metaproc.commands.tools  # noqa: E402, F401
import metaproc.commands.trace  # noqa: E402, F401
import metaproc.commands.validate  # noqa: E402, F401
import metaproc.commands.variants  # noqa: E402, F401
import metaproc.commands.wait  # noqa: E402, F401
import metaproc.commands.write_usage  # noqa: E402, F401


def main() -> None:
    """CLI entry point — wraps Typer with structured error handling."""
    try:
        app()
    except CLIError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        raise SystemExit(exc.exit_code) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
