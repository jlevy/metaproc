"""Enforce that every ``var(--token)`` in our CSS resolves to a definition.

Catches the failure mode of referencing a custom property that was never
declared — e.g. ``var(--scrollbar-width)`` with no ``--scrollbar-width:``
defined anywhere. A browser silently falls back to the property's initial
value when this happens, so the bug only shows up under visual inspection.
This test makes the failure loud at unit-test time.

The host stylesheet and consumer plugin styles share a token namespace at runtime.
The plugin manifest loads ``viz.css`` and ``domain_views.css`` into the same document,
so a token defined in any of these files is reachable from the others.

Only references *this repository* owns are reported. The host stylesheet is still read,
because a metaproc rule may legitimately use a host token and must fail when that token
goes away — but Metabrowser sets several of its own tokens from JavaScript
(``--tree-depth`` per tree row, the file-type colors, the git skeleton row height), so
they are declared nowhere in CSS by design. Reporting those made this gate fail on a
dependency\'s internals, which this repository can neither fix nor should police.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

from metaproc.metabrowser_plugin import plugin_dir

_METABROWSER_ROOT = Path(str(importlib.resources.files("metabrowser")))
_PLUGIN_ROOT = plugin_dir()
# Read for token definitions only: the host supplies tokens the plugin may use.
HOST_CSS_FILES = [_METABROWSER_ROOT / "static" / "styles.css"]
# Read for definitions and checked for references: this repository owns these.
PLUGIN_CSS_FILES = [
    _PLUGIN_ROOT / "styles.css",
    _PLUGIN_ROOT / "viz.css",
    _PLUGIN_ROOT / "domain_views.css",
]
CSS_FILES = [*HOST_CSS_FILES, *PLUGIN_CSS_FILES]

_VAR_REF = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)")
# Match any `--name:` assignment, including multiple per line (the .ft-* block
# packs three declarations into one selector body) and per-class scoped tokens.
# Negative lookbehind on `(` excludes `var(--name)` references from matching.
_VAR_DEF = re.compile(r"(?<!\()(--[a-zA-Z0-9_-]+)\s*:")


def test_every_var_reference_resolves() -> None:
    defined: set[str] = set()
    referenced: dict[str, list[tuple[str, int]]] = {}

    for path in CSS_FILES:
        owned = path in PLUGIN_CSS_FILES
        text = path.read_text()
        # Blank out comment bodies so example tokens inside /* ... */ are ignored
        # without disturbing line numbering — the offender list points back to
        # actual file line numbers, not post-strip line numbers.
        masked = re.sub(
            r"/\*.*?\*/",
            lambda m: re.sub(r"[^\n]", " ", m.group(0)),
            text,
            flags=re.DOTALL,
        )
        for lineno, line in enumerate(masked.splitlines(), start=1):
            for m in _VAR_DEF.finditer(line):
                defined.add(m.group(1))
            if not owned:
                continue
            for m in _VAR_REF.finditer(line):
                referenced.setdefault(m.group(1), []).append((path.name, lineno))

    missing = {name: sites for name, sites in referenced.items() if name not in defined}
    if missing:
        lines = [
            f"  {fname}:{lineno}: var({name})"
            for name in sorted(missing)
            for fname, lineno in missing[name]
        ]
        raise AssertionError(
            "Undefined CSS custom properties referenced (no matching --name: declaration in "
            "the host or consumer plugin styles). Either add the token to :root, or fix the "
            "reference. Offenders:\n" + "\n".join(lines)
        )
