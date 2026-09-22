"""Contracts for publishing a file that must never be readable by another user.

The point of `write_secret_text` is the *instant* a credential is on disk, not the
steady state afterwards, so these tests watch the write rather than its result. A test
that only checks the final mode passes just as happily against
`write_text()` + `chmod()`, which is the shape this module exists to replace.

`filesystem-rules` asks for failure injected before and after the commit point, and for
the atomicity assertion to be made by observing the destination mid-write — not by
checking that the contents are one of two acceptable values, which passes against a
non-atomic implementation whenever the race does not happen to occur.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from metaproc.io import SECRET_FILE_MODE, write_secret_text


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_published_file_is_owner_only(tmp_path: Path) -> None:
    dest = tmp_path / "credentials.json"
    write_secret_text(dest, '{"token": "s3cret"}')

    assert dest.read_text(encoding="utf-8") == '{"token": "s3cret"}'
    assert _mode(dest) == SECRET_FILE_MODE


@pytest.mark.parametrize("mask", [0, 0o777])
def test_mode_holds_under_any_umask(tmp_path: Path, mask: int) -> None:
    """The published mode must not depend on what the umask happens to allow.

    This is a steady-state check and a `write_text` + `chmod` pair would satisfy it
    too — the two tests below are the ones that separate them, by observing the write
    rather than its result.
    """
    previous = os.umask(mask)
    try:
        dest = tmp_path / "credentials.json"
        write_secret_text(dest, "blob")
        assert _mode(dest) == SECRET_FILE_MODE
    finally:
        _ = os.umask(previous)


def test_no_intermediate_file_is_ever_group_or_world_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Watch every file that appears in the directory, not just the destination.

    Atomic publication stages a temp file *beside* the destination, so a staged file
    created at the umask default would leak the secret just as thoroughly as a
    destination at the umask default. The sample is taken at the first `os.write`,
    which is the first moment the staged file exists and can be opened by anyone.
    """
    seen: list[tuple[str, int]] = []
    real_write = os.write

    def sampling_write(fd: int, data: bytes) -> int:
        seen.extend((child.name, _mode(child)) for child in tmp_path.iterdir())
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", sampling_write)

    previous = os.umask(0)
    try:
        write_secret_text(tmp_path / "credentials.json", "blob" * 4096)
    finally:
        _ = os.umask(previous)

    assert seen, "the sampler never fired; the test is not observing anything"
    leaked = [(name, mode) for name, mode in seen if mode & (stat.S_IRWXG | stat.S_IRWXO)]
    assert not leaked, f"a file was readable beyond its owner mid-write: {leaked}"


def test_failure_before_commit_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assertion `filesystem-rules` asks for: inject, then check both properties.

    The failure lands after the staged file exists and has taken some bytes, but
    before the rename — the window in which a non-atomic writer has already damaged
    the destination.
    """
    dest = tmp_path / "credentials.json"
    write_secret_text(dest, "original")

    # The partial payload is a sentinel rather than a prefix of the real content: a
    # prefix can coincide with the previous contents and make this test pass against a
    # non-atomic writer, which is how an atomicity test quietly stops testing anything.
    real_write = os.write
    calls = {"n": 0}

    def failing_write(fd: int, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            _ = real_write(fd, b"partial-write-sentinel")
            raise RuntimeError("injected failure before the commit point")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", failing_write)

    with pytest.raises(RuntimeError, match="injected failure"):
        write_secret_text(dest, "replacement blob " * 500)

    monkeypatch.undo()

    # 1. Observers still see the original destination, unchanged.
    assert dest.read_text(encoding="utf-8") == "original"
    assert _mode(dest) == SECRET_FILE_MODE
    # 2. No success was reported: the raise above is that assertion.
    # 3. The half-written staging file is not left at the published name.
    published = [p.name for p in tmp_path.iterdir() if not p.name.endswith(".partial")]
    assert published == ["credentials.json"]


def test_replacement_is_complete_or_absent_never_partial(tmp_path: Path) -> None:
    dest = tmp_path / "credentials.json"
    write_secret_text(dest, "original")
    write_secret_text(dest, "a much longer replacement blob " * 100)

    assert dest.read_text(encoding="utf-8").startswith("a much longer replacement")
    assert _mode(dest) == SECRET_FILE_MODE
    assert sorted(p.name for p in tmp_path.iterdir()) == ["credentials.json"]


def test_make_parents_is_opt_in(tmp_path: Path) -> None:
    """Defaults to off: a directory holding secrets wants a mode the umask will not give."""
    dest = tmp_path / "missing" / "credentials.json"
    with pytest.raises(OSError):
        write_secret_text(dest, "blob")

    write_secret_text(dest, "blob", make_parents=True)
    assert dest.read_text(encoding="utf-8") == "blob"


def test_non_ascii_round_trips_as_utf8(tmp_path: Path) -> None:
    """Encoding is pinned, not inherited from the platform locale."""
    dest = tmp_path / "credentials.json"
    write_secret_text(dest, '{"label": "café — ✓"}')
    assert dest.read_bytes().decode("utf-8") == '{"label": "café — ✓"}'
