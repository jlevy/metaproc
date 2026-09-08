"""One report for every launch-blocking defect in an invocation.

A launch is refused by four independent checks: unresolved template
placeholders, unresolved operator parameters, missing or unreadable input
files, and unresolvable execution profiles. Each check already aggregates
within itself, but raising on the first non-empty one makes an operator
discover a bad launch serially — one failed launch per class, four launches to
bring up a cohort whose invocation was wrong in four ways from the start.

These helpers run all four and render them as a single grouped message. The
per-item text each check produces is unchanged; only the grouping is new.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from metaproc.engine.build_plan import validate_execution_profiles
from metaproc.engine.input_validation import (
    INPUT_CLASS_PARAM,
    classify_process_input_errors,
)
from metaproc.engine.placeholders import validate_spec_placeholders
from metaproc.models.authored import ProcessSpec

PLACEHOLDER_GROUP = "unresolved placeholders in process spec (pass via --var or set env var)"
PARAM_GROUP = "unresolved operator parameters (pass via --var KEY=VALUE)"
INPUT_FILE_GROUP = "process input validation failed"
PROFILE_GROUP = "invalid execution profile"


def collect_launch_errors(
    spec: ProcessSpec,
    variables: dict[str, str],
    process_dir: Path,
    *,
    process_path: Path,
    profile_files: Sequence[Path] = (),
    adapter_override: str | None = None,
    step_profile_overrides: Mapping[str, str] | None = None,
    validate_inputs: bool = True,
) -> list[tuple[str, list[str]]]:
    """Return ``(group header, messages)`` for every class that has a defect.

    Groups come back in the order an operator meets them: what the templates
    could not resolve, what the command line did not supply, what the filesystem
    did not hold, and what the profile registry did not know. Classes with
    nothing to report are omitted.
    """
    groups: list[tuple[str, list[str]]] = []

    placeholder_errors = validate_spec_placeholders(spec, variables)
    if placeholder_errors:
        groups.append((PLACEHOLDER_GROUP, placeholder_errors))

    if validate_inputs:
        classified = classify_process_input_errors(spec, variables, process_dir)
        param_errors = [msg for cls, msg in classified if cls == INPUT_CLASS_PARAM]
        file_errors = [msg for cls, msg in classified if cls != INPUT_CLASS_PARAM]
        if param_errors:
            groups.append((PARAM_GROUP, param_errors))
        if file_errors:
            groups.append((INPUT_FILE_GROUP, file_errors))

    profile_errors = validate_execution_profiles(
        spec,
        process_path=process_path,
        profile_files=profile_files,
        adapter_override=adapter_override,
        step_profile_overrides=step_profile_overrides,
    )
    if profile_errors:
        groups.append((PROFILE_GROUP, profile_errors))

    return groups


def format_launch_errors(
    groups: Sequence[tuple[str, list[str]]],
    *,
    hint: str = "",
) -> str:
    """Render collected groups as one operator-facing message.

    A single group renders as that group alone, which is what a launch with one
    kind of problem has always printed. Two or more get a count line first, so
    the operator knows up front how many separate fixes the next launch needs.
    """
    blocks = [f"{header}:\n  " + "\n  ".join(messages) for header, messages in groups]
    if len(groups) > 1:
        total = sum(len(messages) for _header, messages in groups)
        preamble = (
            f"launch validation failed: {total} problems across {len(groups)} classes of input"
        )
        body = preamble + "\n\n" + "\n\n".join(blocks)
    else:
        body = "\n\n".join(blocks)
    return f"{body}\n{hint}" if hint else body
