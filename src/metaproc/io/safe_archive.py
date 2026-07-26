"""Bounded, containment-safe tar archive extraction."""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath

DEFAULT_MAX_MEMBERS = 100_000
DEFAULT_MAX_UNPACKED_BYTES = 20 * 1024 * 1024 * 1024


def _validated_members(
    archive: tarfile.TarFile,
    destination: Path,
    *,
    max_members: int,
    max_unpacked_bytes: int,
) -> list[tarfile.TarInfo]:
    """Return members only after validating the complete archive."""
    members = archive.getmembers()
    if len(members) > max_members:
        raise ValueError(
            f"archive has too many members: {len(members)} exceeds limit {max_members}"
        )

    destination = destination.resolve()
    seen: set[str] = set()
    unpacked_bytes = 0
    for member in members:
        member_path = PurePosixPath(member.name)
        if (
            not member.name
            or member_path.is_absolute()
            or ".." in member_path.parts
            or not member_path.parts
            or member_path.parts[0] in {"", "."}
        ):
            raise ValueError(f"unsafe archive member path: {member.name!r}")

        normalized = member_path.as_posix()
        if normalized in seen:
            raise ValueError(f"duplicate archive member: {normalized!r}")
        seen.add(normalized)

        target = (destination / normalized).resolve()
        if not target.is_relative_to(destination):
            raise ValueError(f"unsafe archive member path: {member.name!r}")

        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member type: {member.name!r}")
        if member.size < 0:
            raise ValueError(f"negative archive member size: {member.name!r}")

        unpacked_bytes += member.size
        if unpacked_bytes > max_unpacked_bytes:
            raise ValueError(
                f"archive unpacked size {unpacked_bytes} exceeds limit {max_unpacked_bytes} bytes"
            )
    return members


def safe_extract_tar(
    archive_path: Path,
    destination: Path,
    *,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
) -> None:
    """Safely extract a tar archive after validating every member.

    Only regular files and directories are accepted. Absolute paths, parent
    traversal, links, special files, duplicate paths, excessive member counts,
    and excessive expanded size are rejected before extraction begins.
    """
    if max_members < 1:
        raise ValueError("max_members must be positive")
    if max_unpacked_bytes < 0:
        raise ValueError("max_unpacked_bytes must be non-negative")

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        members = _validated_members(
            archive,
            destination,
            max_members=max_members,
            max_unpacked_bytes=max_unpacked_bytes,
        )
        archive.extractall(destination, members=members, filter="data")


__all__ = [
    "DEFAULT_MAX_MEMBERS",
    "DEFAULT_MAX_UNPACKED_BYTES",
    "safe_extract_tar",
]
