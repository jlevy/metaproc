"""Process-wide logging configuration shared by metaproc's command entrypoints."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

QUIET_HTTP_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")
"""HTTP client loggers held at WARNING under metaproc's INFO root logger.

Code handlers run in the orchestrator's process, so the root level metaproc sets is
theirs too. httpx logs every request at INFO with its full URL, and a Vertex AI URL
names the caller's GCP project, so each model call a handler made would put that
identifier in the run's console output and logs.
"""


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger for a metaproc entrypoint and quiet HTTP clients.

    A level a caller already set on one of ``QUIET_HTTP_LOGGERS`` is kept, so a handler
    or operator that wants request logs can still enable them.
    """
    logging.basicConfig(level=level, format=LOG_FORMAT)
    for name in QUIET_HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.WARNING)
