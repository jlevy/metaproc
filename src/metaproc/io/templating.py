"""Strict ``{{ }}`` document template renderer.

Substitutes ``{{name}}`` placeholders in template text using an explicit values
mapping.  Unlike the engine-level :mod:`metaproc.engine.placeholders`, this
module operates *only* on the caller-supplied ``values`` dict — no environment
fallback, no framework-namespace resolution, no alias chains.

The ``template:`` frontmatter block (status, vars) is metadata about the
template contract.  :func:`strip_template_frontmatter` removes that block from
the rendered output so the final artifact carries only the document's own
frontmatter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Same token pattern used by metaproc.engine.placeholders.
_PLACEHOLDER_RE = re.compile(r"\{\{([\w.]+)\}\}")

# Matches the ``template:`` top-level block inside YAML frontmatter.
# Captures the full ``template:\n  ...\n`` block (indented continuation lines).
_TEMPLATE_FM_RE = re.compile(
    r"^template:\s*\n(?:[ \t]+\S[^\n]*\n)*",
    re.MULTILINE,
)


class TemplateRenderError(ValueError):
    """Raised when template rendering fails due to missing or unused variables."""


def render_template(
    text: str,
    values: Mapping[str, str],
    *,
    strict: bool = True,
) -> str:
    """Substitute ``{{name}}`` placeholders in *text* from *values*.

    Parameters
    ----------
    text:
        Template body (may include frontmatter).
    values:
        Mapping of placeholder name -> replacement string.
    strict:
        When ``True`` (default), raise :class:`TemplateRenderError` if *text*
        contains a placeholder not present in *values*, or if *values* contains
        a key that does not appear as a placeholder in *text*.
        When ``False``, unknown placeholders are left as-is and unused keys
        are silently ignored.

    Returns
    -------
    str
        The rendered text.
    """
    body_placeholders = set(_PLACEHOLDER_RE.findall(text))

    if strict:
        missing = body_placeholders - set(values)
        if missing:
            sorted_missing = sorted(missing)
            msg = (
                f"template has placeholders not present in values: "
                f"{', '.join('{{' + k + '}}' for k in sorted_missing)}"
            )
            raise TemplateRenderError(msg)

        unused = set(values) - body_placeholders
        if unused:
            sorted_unused = sorted(unused)
            msg = (
                f"values contain keys never used as placeholders in the template: "
                f"{', '.join(sorted_unused)}"
            )
            raise TemplateRenderError(msg)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        return match.group(0)  # leave as-is in non-strict mode

    return _PLACEHOLDER_RE.sub(_replace, text)


def strip_template_frontmatter(text: str) -> str:
    """Remove the ``template:`` block from YAML frontmatter.

    If the document starts with ``---\\n`` frontmatter, any top-level
    ``template:`` key and its indented children are removed.  The rest of
    the frontmatter (and the body) is preserved.

    If removing the ``template:`` block leaves the frontmatter empty
    (only whitespace between the ``---`` delimiters), the entire
    frontmatter envelope is removed.
    """
    if not text.startswith("---\n"):
        return text

    # Find the closing --- delimiter.
    end_idx = text.find("\n---\n", 4)
    if end_idx == -1:
        end_idx = text.find("\n---", 4)
        if end_idx == -1:
            return text
        fm_section = text[4 : end_idx + 4]
        after_fm = text[end_idx + 4 :]
    else:
        fm_section = text[4 : end_idx + 1]
        after_fm = text[end_idx + 5 :]  # skip past \n---\n

    cleaned_fm = _TEMPLATE_FM_RE.sub("", fm_section)

    if not cleaned_fm.strip():
        # Frontmatter is empty after removing template block — drop envelope.
        return after_fm.lstrip("\n")

    return "---\n" + cleaned_fm + "---\n" + after_fm
