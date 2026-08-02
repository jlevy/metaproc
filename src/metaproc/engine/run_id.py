"""Compact timestamped run-ID generation with a legacy template escape hatch.

Default form: ``run_{timestamped_uid}``

Uses ``strif.new_timestamped_uid()`` for the timestamped component, which produces
chronologically sortable, globally unique IDs. Process and title are run metadata, not
identity components.
"""

from __future__ import annotations

import re

from strif import new_timestamped_uid

from metaproc.config.env_vars import MetaprocEnv
from metaproc.ids import new_timestamped_typed_id


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
    """Generate a typed run ID, or honor an explicitly configured legacy template.

    Args:
        process_slug: Short identifier for the process (e.g., "mine", "predict").
        title: Optional human-readable title (e.g., "tech mix 500").

    Returns:
        A run ID like ``run_20260408T003012Z-2555210000-foayjjh``.
    """
    template = MetaprocEnv.RUN_ID_TEMPLATE.read_str(default=None)
    title_slug = slugify(title) if title else ""
    if template is None:
        raw = new_timestamped_typed_id("run")
    else:
        raw = template.format_map(
            {
                "process_slug": process_slug,
                "timestamped_uid": new_timestamped_uid(),
                "title_slug": title_slug,
            }
        )
    # Clean up doubled or trailing hyphens (e.g., when title_slug is empty).
    return re.sub(r"-{2,}", "-", raw).strip("-")
