"""Centralized output management for dual text/YAML/JSON output.

All user-facing output goes through OutputManager so commands can
switch between human-readable text, YAML, and structured JSON without
per-command branching.

Default is TEXT, which renders strings as-is and structured values
(dicts/lists) as YAML — Python's ``repr(dict)`` is unhelpful and
parses as neither YAML nor JSON.
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum

from metaproc.config.env_vars import MetaprocEnv
from metaproc.io import to_yaml_string


class OutputFormat(StrEnum):
    TEXT = "text"
    YAML = "yaml"
    JSON = "json"


def _yaml_dump(obj: object) -> str:
    """Return a block-style YAML rendering of *obj* (no leading ``---``).

    ``to_yaml_string`` handles dicts, lists, and primitive scalars.
    Strings render as plain or block scalars per ruamel's heuristics.
    """
    return to_yaml_string(obj).rstrip("\n")


class OutputManager:
    """Routes data to stdout and progress/errors to stderr.

    Respects NO_COLOR, CI env vars, and --no-progress.
    """

    def __init__(
        self,
        fmt: OutputFormat = OutputFormat.TEXT,
        no_progress: bool = False,
    ) -> None:
        self.format: OutputFormat = fmt
        self.no_progress: bool = no_progress
        self.no_color: bool = bool(MetaprocEnv.NO_COLOR.read_str(default=None)) or bool(
            MetaprocEnv.CI.read_str(default=None)
        )

    def data(self, obj: object) -> None:
        """Write structured data to stdout."""
        if self.format == OutputFormat.JSON:
            json.dump(obj, sys.stdout, default=str)
            sys.stdout.write("\n")
        elif self.format == OutputFormat.YAML:
            sys.stdout.write(_yaml_dump(obj) + "\n")
        else:  # TEXT — strings as-is, structured values as YAML.
            if isinstance(obj, str):
                sys.stdout.write(obj + "\n")
            else:
                sys.stdout.write(_yaml_dump(obj) + "\n")
        sys.stdout.flush()

    def summary(self, line: str, *, record: object | None = None) -> None:
        """Emit a one-line human summary in TEXT, full record in JSON/YAML.

        TEXT output prefers the human-readable ``line`` (kept short
        and scannable — typical shape: ``"✓ <subject>  k=v  k=v"``).
        Machine modes (JSON / YAML) prefer the full ``record`` so
        downstream automation never loses fields a human-readable
        summary chose to elide. When ``record`` is None, JSON / YAML
        fall back to ``{"message": line}``.

        Use this instead of :meth:`data` for "operation-result"
        emissions where humans want a one-liner but automation wants
        the structured record. :meth:`data` stays the right call for
        list / table dumps where the YAML rendering IS the human
        format.
        """
        if self.format == OutputFormat.JSON:
            payload = record if record is not None else {"message": line}
            json.dump(payload, sys.stdout, default=str)
            sys.stdout.write("\n")
        elif self.format == OutputFormat.YAML:
            payload = record if record is not None else {"message": line}
            sys.stdout.write(_yaml_dump(payload) + "\n")
        else:  # TEXT
            sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def progress(self, message: str) -> None:
        """Write progress info to stderr (suppressed when --no-progress)."""
        if not self.no_progress:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()

    def error(self, message: str) -> None:
        """Write error message to stderr."""
        sys.stderr.write(f"Error: {message}\n")
        sys.stderr.flush()

    def warning(self, message: str) -> None:
        """Write warning message to stderr (not suppressed by --no-progress)."""
        sys.stderr.write(f"Warning: {message}\n")
        sys.stderr.flush()
