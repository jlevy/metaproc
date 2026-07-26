"""Run ID generation from a configurable template.

Default template: ``{process_slug}-{timestamped_uid}-{title_slug}``

Uses ``strif.new_timestamped_uid()`` for the timestamped component — produces
IDs like ``20260408T003012Z-2555210000-foayjjh`` that sort chronologically and
are globally unique.
"""

from __future__ import annotations

import re

from strif import new_timestamped_uid

from metaproc.config.env_vars import MetaprocEnv

_DEFAULT_TEMPLATE = "{process_slug}-{timestamped_uid}-{title_slug}"


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL/path-safe slug: lowercase, hyphenated, truncated."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:max_length].rstrip("-")


def generate_run_id(
    process_slug: str,
    title: str = "",
) -> str:
    """Generate a run ID from the template.

    Args:
        process_slug: Short identifier for the process (e.g., "mine", "predict").
        title: Optional human-readable title (e.g., "tech mix 500").

    Returns:
        A run ID string like ``mine-20260408T003012Z-2555210000-foayjjh-tech-mix-500``.
    """
    template = MetaprocEnv.RUN_ID_TEMPLATE.read_str(default=_DEFAULT_TEMPLATE)
    title_slug = slugify(title) if title else ""

    raw = template.format(
        process_slug=process_slug,
        timestamped_uid=new_timestamped_uid(),
        title_slug=title_slug,
    )
    # Clean up doubled or trailing hyphens (e.g., when title_slug is empty).
    return re.sub(r"-{2,}", "-", raw).strip("-")
