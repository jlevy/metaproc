"""Health monitoring utilities for the run pool.

Pure functions for health limit evaluation.  The stateful orchestration
(hysteresis counters, pressure monitor loop, dual-governor controller)
lives in ``pool.py``; see ``RunPoolConfig`` for the full adaptive
concurrency policy.
"""

from __future__ import annotations


def check_rss_limit(rss_bytes: int | None, max_rss_bytes: int | None) -> bool:
    """Return True if RSS exceeds the configured limit."""
    if max_rss_bytes is None or rss_bytes is None:
        return False
    return rss_bytes > max_rss_bytes


def check_log_limit(log_bytes: int | None, max_log_bytes: int | None) -> bool:
    """Return True if log file size exceeds the configured limit."""
    if max_log_bytes is None or log_bytes is None:
        return False
    return log_bytes > max_log_bytes


def check_descendants_limit(descendants: int | None, max_descendants: int | None) -> bool:
    """Return True if descendant count exceeds the configured limit."""
    if max_descendants is None or descendants is None:
        return False
    return descendants > max_descendants
