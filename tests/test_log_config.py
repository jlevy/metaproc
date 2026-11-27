"""Metaproc's INFO root logger does not pass HTTP clients' request URLs through.

Code handlers run in the orchestrator's process, and httpx logs each request's full
URL at INFO. A Vertex AI URL names the caller's GCP project.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from metaproc.utils.log_config import QUIET_HTTP_LOGGERS, configure_logging


@pytest.fixture
def restore_http_loggers() -> Iterator[None]:
    levels = {name: logging.getLogger(name).level for name in QUIET_HTTP_LOGGERS}
    yield
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.usefixtures("restore_http_loggers")
def test_http_client_request_logs_are_held_at_warning() -> None:
    for name in QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    configure_logging()
    for name in QUIET_HTTP_LOGGERS:
        logger = logging.getLogger(name)
        assert not logger.isEnabledFor(logging.INFO)
        assert logger.isEnabledFor(logging.WARNING)


@pytest.mark.usefixtures("restore_http_loggers")
def test_an_explicit_http_client_level_is_kept() -> None:
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    configure_logging()
    assert logging.getLogger("httpx").level == logging.DEBUG
