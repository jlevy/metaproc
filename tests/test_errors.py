"""Tests for metaproc.errors — structured error types."""

from __future__ import annotations

import pytest

from metaproc.errors import CLIError, ValidationError


def test_cli_error_default_exit_code():
    err = CLIError("something broke")
    assert str(err) == "something broke"
    assert err.exit_code == 1


def test_cli_error_custom_exit_code():
    err = CLIError("custom", exit_code=42)
    assert err.exit_code == 42


def test_validation_error_exit_code_2():
    err = ValidationError("bad input")
    assert str(err) == "bad input"
    assert err.exit_code == 2


def test_validation_error_is_cli_error():
    err = ValidationError("oops")
    assert isinstance(err, CLIError)


def test_cli_error_is_exception():
    with pytest.raises(CLIError, match="boom"):
        raise CLIError("boom")
