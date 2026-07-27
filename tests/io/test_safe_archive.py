"""Security contracts for tar archive extraction."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from metaproc.io.safe_archive import safe_extract_tar


def _write_tar(path: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            payload = b"payload" if member.isfile() else None
            archive.addfile(member, io.BytesIO(payload) if payload is not None else None)


def _file_member(name: str, *, size: int = 7) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.size = size
    return member


def test_safe_extract_tar_extracts_regular_files(tmp_path: Path) -> None:
    archive = tmp_path / "safe.tar.gz"
    _write_tar(archive, [_file_member("nested/data.txt")])

    destination = tmp_path / "out"
    safe_extract_tar(archive, destination)

    assert (destination / "nested" / "data.txt").read_bytes() == b"payload"


@pytest.mark.parametrize("name", [".", "../escape.txt", "/absolute.txt", "nested/../../escape.txt"])
def test_safe_extract_tar_rejects_paths_outside_destination(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    _write_tar(archive, [_file_member(name)])

    with pytest.raises(ValueError, match="unsafe archive member path"):
        safe_extract_tar(archive, tmp_path / "out")

    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_safe_extract_tar_rejects_links(tmp_path: Path, member_type: bytes) -> None:
    member = tarfile.TarInfo("link")
    member.type = member_type
    member.linkname = "target"
    archive = tmp_path / "link.tar.gz"
    _write_tar(archive, [member])

    with pytest.raises(ValueError, match="links are not allowed"):
        safe_extract_tar(archive, tmp_path / "out")


def test_safe_extract_tar_rejects_special_members(tmp_path: Path) -> None:
    member = tarfile.TarInfo("device")
    member.type = tarfile.CHRTYPE
    archive = tmp_path / "device.tar.gz"
    _write_tar(archive, [member])

    with pytest.raises(ValueError, match="unsupported archive member type"):
        safe_extract_tar(archive, tmp_path / "out")


def test_safe_extract_tar_rejects_duplicate_paths(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.tar.gz"
    _write_tar(archive, [_file_member("same.txt"), _file_member("same.txt")])

    with pytest.raises(ValueError, match="duplicate archive member"):
        safe_extract_tar(archive, tmp_path / "out")


def test_safe_extract_tar_enforces_member_and_size_limits(tmp_path: Path) -> None:
    archive = tmp_path / "limits.tar.gz"
    _write_tar(archive, [_file_member("one.txt"), _file_member("two.txt")])

    with pytest.raises(ValueError, match="too many members"):
        safe_extract_tar(archive, tmp_path / "members", max_members=1)
    with pytest.raises(ValueError, match="unpacked size"):
        safe_extract_tar(archive, tmp_path / "bytes", max_unpacked_bytes=10)
