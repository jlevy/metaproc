"""Reject private workspace residue before Metaproc is published."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 30
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 3
SKIP_PARTS = {".git", ".venv", "dist", "node_modules", "__pycache__"}
SKIP_ROOTS = (ROOT / ".tbd" / "docs",)
TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_-]*", re.IGNORECASE)
TOKEN_COMPONENT_PATTERN = re.compile(r"[a-z][a-z0-9]*", re.IGNORECASE)
ASCII_RUN_PATTERN = re.compile(rb"[\x20-\x7e]{4,}")
ISSUE_ID_PATTERN = re.compile(r"\b([a-z][a-z0-9]{1,15})-[a-z0-9]{4,}\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PUBLIC_CONTACT_EMAILS = {"joshua@cal.berkeley.edu"}
REAL_FIXTURE_FIELD_PATTERN = re.compile(
    r'["\']?(?:company|customer|symbol|ticker)["\']?\s*[:=]\s*["\']?(?!synthetic-)[A-Za-z0-9]',
    re.IGNORECASE,
)

# Values are SHA-256 hashes so the hygiene gate does not publish the private
# vocabulary it is designed to reject.
BANNED_TOKEN_HASHES: dict[str, str] = {
    "31523fdddcd5294e2bc6cc775fba79e2597e6d45bc0a995c548493038ebea33f": "private name",
    "505030f438f746617d86174f0a21b70997f9b84ae415c1e31708f91711e5f189": "private name",
    "0035e3bed3a10ebe81bc85bbf80cc092871ba344926efa491f195e36cc7e003b": "private name",
    "2d53000528ed0be7f64e8498c577123bb9bbbb560223db2e0f70dd690e2b1aa1": "private name",
    "b526066864bb66f02f74b77aca24fa328c325c6f6d71f6efbf9957b874490b55": "private name",
    "e498282813b5fd5e888a0b7ea0a2da392447b2f9bb3a3f8cecbec00c27a0f1b9": "private name",
    "96b5511d77cf16dc6d453c6275a0e3756f1893b425f8b4423e386aff6da4172d": "private name",
    "8964f8db8aa69294f076f9a4163ddf7b54bece9177f099c0b68cea159c07e656": "private name",
    "92d66e877d3142ea5b9ff267347b44a3d3a07fff6b666a84604e361555a6c98d": "private name",
    "f653a6c9123f6c82925db89bc474bb6642474d28e30fa7f75928e0ea48bfb302": "private name",
    "d875563e5fa8fff6c67736bdfa5b8b4e3c4cc624a562b6b35baae20b68a388c6": "private name",
    "0b26b4aef2cb20e29d2d121c67f109267ab87913e1cb2dc0f0ba07d91056e516": "private name",
    "4443ebe6787ef8c1d6c4be955c4f783171707c0d3a10bc19af8a8e98115f6244": "private name",
    "28afbf0889041b38ca15dfdb65e8bd7b657646923e806223b978f2a49cd66519": "private name",
}
PRIVATE_ISSUE_PREFIX_HASHES = frozenset(
    {"0035e3bed3a10ebe81bc85bbf80cc092871ba344926efa491f195e36cc7e003b"}
)
BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private pull-request reference", re.compile(r"\bPR[- ]?#?\d+\b", re.IGNORECASE)),
    ("private home path", re.compile(r"/(?:Users|home)/[^/\s]+(?=/|\s|$)")),
    (
        "credential material",
        re.compile(
            r"(?:gh[opsu]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
        ),
    ),
    (
        "concrete service account",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com\b"),
    ),
)


def _token_findings(source: str, text: str) -> list[str]:
    findings: list[str] = []
    combined = f"{source}\n{text}"
    matches = list(TOKEN_PATTERN.finditer(combined)) + list(
        TOKEN_COMPONENT_PATTERN.finditer(combined)
    )
    for match in matches:
        digest = hashlib.sha256(match.group(0).lower().encode()).hexdigest()
        label = BANNED_TOKEN_HASHES.get(digest)
        if label is not None:
            offset = max(0, match.start() - len(source) - 1)
            line = text.count("\n", 0, offset) + 1
            findings.append(f"{source}:{line}: {label}")
    for match in ISSUE_ID_PATTERN.finditer(combined):
        prefix_digest = hashlib.sha256(match.group(1).lower().encode()).hexdigest()
        if prefix_digest in PRIVATE_ISSUE_PREFIX_HASHES:
            offset = max(0, match.start() - len(source) - 1)
            line = text.count("\n", 0, offset) + 1
            findings.append(f"{source}:{line}: copied issue identifier")
    for match in EMAIL_PATTERN.finditer(combined):
        if match.group(0).lower() in PUBLIC_CONTACT_EMAILS:
            continue
        domain = match.group(1).lower()
        if domain.endswith(("example.invalid", "users.noreply.github.com")):
            continue
        offset = max(0, match.start() - len(source) - 1)
        line = text.count("\n", 0, offset) + 1
        findings.append(f"{source}:{line}: potential personal email address")
    return findings


def find_hygiene_findings(source: str, text: str) -> list[str]:
    """Return public-hygiene findings for a path, text file, or archive member."""
    findings = _token_findings(source, text)
    combined = f"{source}\n{text}"
    for label, pattern in BANNED_PATTERNS:
        for match in pattern.finditer(combined):
            offset = max(0, match.start() - len(source) - 1)
            line = text.count("\n", 0, offset) + 1
            findings.append(f"{source}:{line}: {label}")
    if "tests/fixtures/" in source.replace("\\", "/"):
        for match in REAL_FIXTURE_FIELD_PATTERN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{source}:{line}: non-synthetic customer or ticker fixture")
    return findings


def find_binary_findings(source: str, data: bytes) -> list[str]:
    """Scan printable strings embedded in an otherwise binary asset."""
    findings: list[str] = []
    findings.extend(find_hygiene_findings(source, ""))
    for match in ASCII_RUN_PATTERN.finditer(data):
        findings.extend(find_hygiene_findings(source, match.group().decode("ascii")))
    return sorted(set(findings))


def _safe_member_name(name: str) -> bool:
    member = PurePosixPath(name)
    return (
        bool(name) and bool(member.parts) and not member.is_absolute() and ".." not in member.parts
    )


def _scan_member(source: str, data: bytes, *, depth: int = 0) -> list[str]:
    if depth < MAX_ARCHIVE_DEPTH:
        if zipfile.is_zipfile(io.BytesIO(data)):
            return _scan_zip_bytes(source, data, depth=depth + 1)
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
                pass
        except tarfile.TarError:
            pass
        else:
            return _scan_tar_bytes(source, data, depth=depth + 1)
        if data.startswith(b"\x1f\x8b"):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                    unpacked = stream.read(MAX_MEMBER_BYTES + 1)
            except (EOFError, gzip.BadGzipFile, OSError):
                pass
            else:
                if len(unpacked) > MAX_MEMBER_BYTES:
                    return [f"{source}: compressed payload exceeds hygiene size limit"]
                return _scan_member(f"{source}!gzip-payload", unpacked, depth=depth + 1)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return find_binary_findings(source, data)
    return find_hygiene_findings(source, text)


def _scan_tar_bytes(source: str, data: bytes, *, depth: int = 0) -> list[str]:
    findings = find_hygiene_findings(source, "")
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            return [f"{source}: archive member count exceeds hygiene limit"]
        for member in members:
            member_source = f"{source}!{member.name}"
            if not _safe_member_name(member.name):
                findings.append(f"{member_source}: unsafe archive member path")
                continue
            findings.extend(find_hygiene_findings(member_source, ""))
            if member.issym() or member.islnk():
                findings.append(f"{member_source}: archive link is not allowed")
                continue
            if member.isdir():
                continue
            if not member.isfile():
                findings.append(f"{member_source}: unsupported archive member type")
                continue
            total += member.size
            if member.size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                findings.append(f"{member_source}: archive member exceeds hygiene size limit")
                continue
            stream = archive.extractfile(member)
            if stream is not None:
                findings.extend(
                    _scan_member(
                        member_source,
                        stream.read(MAX_MEMBER_BYTES + 1),
                        depth=depth,
                    )
                )
    return findings


def _scan_zip_bytes(source: str, data: bytes, *, depth: int = 0) -> list[str]:
    findings = find_hygiene_findings(source, "")
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            return [f"{source}: archive member count exceeds hygiene limit"]
        for member in members:
            member_source = f"{source}!{member.filename}"
            if not _safe_member_name(member.filename):
                findings.append(f"{member_source}: unsafe archive member path")
                continue
            findings.extend(find_hygiene_findings(member_source, ""))
            if member.is_dir():
                continue
            total += member.file_size
            if member.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                findings.append(f"{member_source}: archive member exceeds hygiene size limit")
                continue
            findings.extend(_scan_member(member_source, archive.read(member), depth=depth))
    return findings


def scan_file(path: Path, *, source: str | None = None) -> list[str]:
    """Scan one text, binary, wheel, sdist, or generic archive file."""
    source = source or path.as_posix()
    data = path.read_bytes()
    return _scan_member(source, data)


def _git_ignored(root: Path, paths: list[Path]) -> set[Path]:
    """Return the subset of ``paths`` ignored by Git."""
    if not paths:
        return set()
    stdin = "".join(f"{path.relative_to(root)}\0" for path in paths)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode not in (0, 1):
        return set()
    return {root / path for path in result.stdout.split("\0") if path}


def repository_files(root: Path = ROOT) -> list[Path]:
    """Return tracked and nonignored untracked files, including binary assets."""
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path != Path(__file__).resolve()
        and not any(part in SKIP_PARTS for part in path.parts)
        and not path.is_relative_to(root / ".tbd" / "docs")
        and not any(path.is_relative_to(skip_root) for skip_root in SKIP_ROOTS)
    ]
    ignored = _git_ignored(root, candidates)
    return [path for path in candidates if path not in ignored]


def scan_git_history(root: Path = ROOT) -> list[str]:
    """Scan every reachable ref name, commit subject, and commit body."""
    findings: list[str] = []
    commands = (
        ("refs", ["git", "-C", str(root), "for-each-ref", "--format=%(refname)"]),
        ("commits", ["git", "-C", str(root), "log", "--all", "--format=%H%n%s%n%b"]),
    )
    for label, command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            findings.append(f"git-{label}: unable to scan reachable Git metadata")
            continue
        findings.extend(find_hygiene_findings(f"git-{label}", result.stdout))
    return findings


def run_hygiene_scan(
    *,
    root: Path = ROOT,
    artifacts: tuple[Path, ...] = (),
    include_git_history: bool = True,
) -> list[str]:
    """Run the complete repository, artifact, and optional history scan."""
    findings: list[str] = []
    for path in repository_files(root):
        findings.extend(scan_file(path, source=str(path.relative_to(root))))
    for artifact in artifacts:
        findings.extend(scan_file(artifact, source=artifact.name))
    if include_git_history:
        findings.extend(scan_git_history(root))
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", action="append", default=[], type=Path)
    parser.add_argument("--skip-git-history", action="store_true")
    args = parser.parse_args()
    findings = run_hygiene_scan(
        artifacts=tuple(args.artifact), include_git_history=not args.skip_git_history
    )
    if findings:
        print("Public hygiene check failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Public hygiene checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
