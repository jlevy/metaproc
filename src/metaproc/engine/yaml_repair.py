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

**Sibling pass:** ``engine.schema_conform`` runs immediately after this one over
the same artifacts, under the same agent-only scoping rule, and answers the
next question: given a document that parses, does each scalar say the type its
contract asks for? Repair has no schema and cannot ask that; conform assumes a
parseable document and does not fix one. Read both module docstrings before
adding a call site to either.

**Do NOT use this for human-authored or programmatically-generated YAML.**
If your code or a fixture is producing invalid YAML, fix it at the source —
calling ``repair_frontmatter_file`` masks the bug. The correct call sites are
exclusively the agent output-validation paths — ``repair_declared_outputs``
in ``engine.validation``, run just before declared-output validation in
``run_parallel`` and ``run_process`` — where the input is a freshly-emitted
LLM artifact.

**Upstream question:** should workflows generate YAML from language models at all
(JSON is more constrained, schema-aware generation could avoid the problem,
two-pass generation with a deterministic templater could be safer). This
module is a stopgap until that lands.

**Parser harmonization:** the self-check uses ``frontmatter_format``'s guarded
safe reader, matching downstream validation. A raw ruamel C scanner can reject
valid flow collections that the guarded reader accepts through its pure-parser
fallback. Such documents must remain unchanged, while malformed scalars still
receive the narrow repair below.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from frontmatter_format import from_yaml_string
from ruamel.yaml.error import YAMLError as RuamelYAMLError
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
from strif import atomic_output_file

log = logging.getLogger(__name__)

# Best-effort repair must not repeatedly parse a large document once per field.
# Eight corrections allow ordinary agent mistakes while bounding full-document work.
_MAX_SCALAR_REPAIRS = 8


def _ruamel_safe_load(text: str) -> None:
    """Check YAML with the guarded safe reader used by downstream validation."""
    from_yaml_string(text)


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
    `"store near me" search interest accelerated...`, which ruamel parses as
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

    Tries to parse the frontmatter with the downstream validator's guarded
    safe reader. If parsing succeeds, returns False (no
    repair needed). On a syntax error, quotes only the scalar on the parser's
    failing line and rechecks the full document. At most eight corrections are
    attempted. Unsupported or over-budget input remains byte-for-byte unchanged.

    Returns True if a repair was applied, False otherwise.
    """
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")

    # Find frontmatter boundaries
    if not content.startswith("---\n"):
        return False
    end_idx = content.index("\n---\n", 4) if "\n---\n" in content[4:] else -1
    if end_idx == -1:
        return False

    frontmatter = content[4 : end_idx + 1]  # between the --- markers

    # The parser's full-document context distinguishes a malformed scalar from
    # valid flow collections, anchored values, comments and block-scalar bodies.
    # Never sweep sibling lines merely because one scalar made the document fail.
    lines = frontmatter.split("\n")
    changed_lines: set[int] = set()
    repaired_frontmatter = frontmatter
    for _ in range(_MAX_SCALAR_REPAIRS + 1):
        repaired_frontmatter = "\n".join(lines)
        try:
            _ruamel_safe_load(repaired_frontmatter)
            break
        except (ParserError, ScannerError) as error:
            mark = error.problem_mark
            if mark is None or len(changed_lines) == _MAX_SCALAR_REPAIRS:
                return False
            line_index = mark.line
            if not 0 <= line_index < len(lines) or line_index in changed_lines:
                return False
            match = _PROBLEMATIC_VALUE_RE.match(lines[line_index])
            if match is None or not _value_needs_quoting(match.group(2)):
                return False
            prefix = lines[line_index][: match.start(2)]
            lines[line_index] = f"{prefix}{_quote_value(match.group(2))}"
            changed_lines.add(line_index)
        except RuamelYAMLError:
            # Duplicate keys, unsafe tags and other semantic errors are not
            # malformed scalars. Refuse without publishing any partial changes.
            return False

    if not changed_lines:
        return False

    # Write repaired content back
    repaired_content = f"---\n{repaired_frontmatter}---\n{content[end_idx + 5 :]}"
    with atomic_output_file(path) as tmp:
        Path(tmp).write_text(repaired_content, encoding="utf-8")
    log.info("Repaired YAML frontmatter in %s", path)
    return True
