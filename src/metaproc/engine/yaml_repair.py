"""YAML frontmatter repair — workaround for LLM-generated YAML only.

**Scope:** This module is a band-aid for the *unavoidable* case of YAML
frontmatter produced by an LLM agent (e.g. Claude / Codex / pi-cli generating
a step output that includes structured frontmatter). LLMs sometimes emit
YAML that fails strict parsing — most commonly values with unquoted
colons (e.g. inline notes like ``(Note: actually Q1)``) or quoted phrases
inside otherwise-plain scalars (e.g. ``"near me" Trends accelerated``), but
also unclosed lists, special-char gotchas (``#`` ``@`` ``*`` ``&``), and
tab/space mixing. This repair function attempts a narrow fix (quote unsafe
plain scalar values) so a single LLM mistake does not cascade into a
permanent-failure → step-FAILED → tier-aborted blast radius.

**Do NOT use this for human-authored or programmatically-generated YAML.**
If your code or a fixture is producing invalid YAML, fix it at the source —
calling ``repair_frontmatter_file`` masks the bug. The correct call sites are
exclusively the agent output-validation path (see
``metaproc/src/metaproc/commands/run_parallel.py`` around the agent-output
validate block) where the input is a freshly-emitted LLM artifact.

**Upstream questions (tracked):** see ``internal issue`` for the open
investigation into whether we should be generating YAML from LLMs at all
(JSON is more constrained, schema-aware generation could avoid the problem,
two-pass generation with a deterministic templater could be safer). This
module is a stopgap until that lands.

**Parser harmonization (internal issue):** downstream validation (via
``frontmatter_format`` → ``ruamel.yaml``) is stricter than PyYAML. A document
that passed PyYAML's ``yaml.safe_load`` could still fail ``ruamel.yaml``
validation, so the operator would see "repaired YAML" log lines followed by
``invalid_outputs`` failures. We use ``ruamel.yaml`` for the self-check so
the repair's pass/fail decision tracks the validator's strictness.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError as RuamelYAMLError
from strif import atomic_output_file

log = logging.getLogger(__name__)


def _ruamel_safe_load(text: str) -> None:
    """Parse YAML via ruamel.yaml YAML(typ='safe') — the same path the
    downstream frontmatter validator uses. Raises RuamelYAMLError on failure.
    """
    YAML(typ="safe").load(io.StringIO(text))


# Matches a YAML key-value line where the value portion contains an unquoted
# colon-space (`: `) that isn't inside a YAML comment.
# Groups: (indent)(key)(value_with_colon)
_PROBLEMATIC_VALUE_RE = re.compile(
    r"^(\s+\w[\w_]*): "  # indent + key + colon-space
    r"(.+)"  # the value
)


def _value_needs_quoting(value: str) -> bool:
    """Check if a YAML value contains patterns that need quoting.

    A value needs quoting if it contains `: ` (colon-space) which YAML
    would interpret as a nested mapping, or if it starts with a quoted phrase
    and then continues outside the quote. LLMs often write values such as
    `"Ollies near me" Google Trends accelerated...`, which ruamel parses as
    an unterminated double-quoted scalar.
    """
    stripped = value.strip()
    # Already quoted
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("'") and stripped.endswith("'")
    ):
        return False
    # Null, boolean, numeric — don't touch
    if stripped in ("null", "true", "false", "~", ""):
        return False
    # Has a YAML comment — split on first unquoted #
    # Check only the value part (before any # comment)
    comment_match = re.search(r"\s+#\s", stripped)
    value_part = stripped[: comment_match.start()] if comment_match else stripped
    # Needs quoting if the value part contains `: ` (a second colon-space)
    if ": " in value_part:
        return True
    return _starts_with_unbalanced_quoted_phrase(value_part)


def _starts_with_unbalanced_quoted_phrase(value: str) -> bool:
    """Return True for malformed scalars like ``"foo" bar``.

    The whole value is not a valid quoted scalar, but the leading quote makes
    the YAML parser try to parse it as one. Quoting the entire value and
    escaping the inner quotes is the least invasive repair.
    """
    return (
        len(value) >= 2
        and value[0] in {'"', "'"}
        and not value.endswith(value[0])
        and value[0] in value[1:]
    )


def _quote_value(value: str) -> str:
    """Wrap a YAML value in double quotes, preserving any trailing comment."""
    stripped = value.strip()
    # Separate value from trailing comment
    comment_match = re.search(r"\s+(#\s.*)$", stripped)
    if comment_match:
        value_part = stripped[: comment_match.start()]
        comment_part = " " + comment_match.group(1)
    else:
        value_part = stripped
        comment_part = ""
    # Escape existing double quotes in the value
    escaped = value_part.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"{comment_part}'


def repair_frontmatter_file(path: Path) -> bool:
    """Attempt to repair YAML frontmatter in a markdown file.

    **Intended use: LLM-generated artifacts only** (see module docstring).
    Calling this on human-authored or programmatically-generated YAML masks
    real bugs — fix invalid YAML at its source instead.

    Tries to parse the frontmatter with ruamel.yaml (the same parser the
    downstream validator uses). If parsing succeeds, returns False (no
    repair needed). If parsing fails, attempts to quote problematic values
    and writes the repaired content back, then re-verifies.

    Returns True if a repair was applied, False otherwise.
    """
    if not path.exists():
        return False

    content = path.read_text()

    # Find frontmatter boundaries
    if not content.startswith("---\n"):
        return False
    end_idx = content.index("\n---\n", 4) if "\n---\n" in content[4:] else -1
    if end_idx == -1:
        return False

    frontmatter = content[4 : end_idx + 1]  # between the --- markers

    # Try parsing first — if it works, no repair needed.
    # Use ruamel.yaml (the validator's parser) so we don't waste a repair
    # cycle on a document PyYAML accepts but the validator will reject.
    try:
        _ruamel_safe_load(frontmatter)
        return False
    except RuamelYAMLError:
        pass

    # Attempt repair: quote values with problematic colons
    lines = frontmatter.split("\n")
    repaired_lines: list[str] = []
    changed = False

    for line in lines:
        m = _PROBLEMATIC_VALUE_RE.match(line)
        if m:
            prefix = m.group(0)[: m.start(2) - m.start(0)]  # "  key: "
            value = m.group(2)
            if _value_needs_quoting(value):
                repaired_lines.append(f"{prefix}{_quote_value(value)}")
                changed = True
                continue
        repaired_lines.append(line)

    if not changed:
        return False

    repaired_frontmatter = "\n".join(repaired_lines)

    # Verify the repair actually fixes the YAML against the SAME parser
    # the downstream validator uses (ruamel.yaml). Using PyYAML here would
    # let through documents the validator rejects, defeating the repair.
    try:
        _ruamel_safe_load(repaired_frontmatter)
    except RuamelYAMLError:
        log.warning("YAML repair attempted but still fails for %s", path)
        return False

    # Write repaired content back
    repaired_content = f"---\n{repaired_frontmatter}---\n{content[end_idx + 5 :]}"
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(repaired_content)
    log.info("Repaired YAML frontmatter in %s", path)
    return True
