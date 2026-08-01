"""Negative and positive contracts for the public hygiene gate."""

from __future__ import annotations

import gzip
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from devtools.public_hygiene import (
    _git_ignored,
    find_binary_findings,
    find_git_metadata_findings,
    find_hygiene_findings,
    repository_files,
    scan_file,
    scan_git_history,
)


def test_public_metaproc_issue_tracking_language_is_allowed() -> None:
    assert find_hygiene_findings("AGENTS.md", "Track this as metaproc-abcd.") == []


def test_private_names_issue_ids_paths_and_credentials_are_rejected() -> None:
    private_package = "earnings" + "_predictions"
    private_issue = "trad" + "ing-abcd"
    private_path = "/" + "Users/alice/work/repo"
    credential = "ghp_" + "a" * 32

    findings = find_hygiene_findings(
        f"fixtures/{private_package}/record.json",
        f"{private_issue}\n{private_path}\n{credential}",
    )

    assert any("private name" in finding for finding in findings)
    assert any("copied issue identifier" in finding for finding in findings)
    assert any("private home path" in finding for finding in findings)
    assert any("credential material" in finding for finding in findings)


def test_synthetic_placeholders_are_allowed() -> None:
    text = (
        "gs://example-bucket/demo/workspace.tar.gz\n"
        "projects/example-project/locations/us-central1\n"
        "synthetic-customer-001"
    )
    assert find_hygiene_findings("tests/fixtures/synthetic-record.json", text) == []


def test_personal_email_and_real_ticker_fixture_are_rejected() -> None:
    email = "person" + "@customer.test"
    record = '{"ticker": "' + "ACME" + '", "contact": "' + email + '"}'

    findings = find_hygiene_findings("tests/fixtures/record.json", record)

    assert any("potential personal email address" in finding for finding in findings)
    assert any("non-synthetic customer or ticker fixture" in finding for finding in findings)


def test_public_package_contact_email_is_allowed() -> None:
    assert (
        find_hygiene_findings(
            "pyproject.toml",
            'maintainers = [{ name = "Joshua Levy", email = "joshua@cal.berkeley.edu" }]',
        )
        == []
    )


def test_git_metadata_allows_attribution_trailers_and_public_pr_references() -> None:
    public_pr = "PR " + "#3"
    agent_email = "agent" + "@anthropic.com"
    body_email = "person" + "@customer.test"
    text = (
        f"Document the public review for {public_pr}.\n\n"
        f"Co-authored-by: Example Agent <{agent_email}>\n"
        f"Contact: {body_email}\n"
    )

    findings = find_git_metadata_findings("git-commits", text)

    assert findings == ["git-commits:4: potential personal email address"]


def test_binary_assets_are_scanned_for_printable_private_residue() -> None:
    private_name = ("fin" + "term").encode()
    findings = find_binary_findings("static/image.bin", b"\x00\xff" + private_name + b"\x00")
    assert any("private name" in finding for finding in findings)


def test_zip_and_tar_members_are_scanned(tmp_path: Path) -> None:
    private_name = "earnings" + "-predictions"
    zip_path = tmp_path / "artifact.whl"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("pkg/data.txt", private_name)

    tar_path = tmp_path / "artifact.tar.gz"
    payload = private_name.encode()
    with tarfile.open(tar_path, "w:gz") as archive:
        member = tarfile.TarInfo("pkg/data.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    assert any("private name" in finding for finding in scan_file(zip_path))
    assert any("private name" in finding for finding in scan_file(tar_path))


def test_nested_and_standalone_gzip_payloads_are_scanned(tmp_path: Path) -> None:
    private_name = ("fin" + "term").encode()
    gzip_path = tmp_path / "record.json.gz"
    gzip_path.write_bytes(gzip.compress(private_name))
    nested_path = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested_path, "w") as archive:
        archive.writestr("record.json.gz", gzip.compress(private_name))

    assert any("private name" in finding for finding in scan_file(gzip_path))
    assert any("private name" in finding for finding in scan_file(nested_path))


def test_archive_member_paths_and_links_are_rejected(tmp_path: Path) -> None:
    tar_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        traversal = tarfile.TarInfo("../escape")
        traversal.size = 0
        archive.addfile(traversal, io.BytesIO())
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        archive.addfile(link)

    findings = scan_file(tar_path)
    assert any("unsafe archive member path" in finding for finding in findings)
    assert any("archive link is not allowed" in finding for finding in findings)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "user@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True)


def test_nonignored_untracked_files_are_scanned_but_ignored_files_are_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "tracked.txt").write_text("public")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    ignored = root / "ignored.txt"
    ignored.write_text("ignored")
    untracked = root / "new.txt"
    untracked.write_text("new")

    assert _git_ignored(root, [ignored, untracked]) == {ignored}
    files = repository_files(root)
    assert untracked in files
    assert ignored not in files


def test_reachable_git_refs_and_commit_messages_are_scanned(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "public.txt").write_text("public")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    private_message = "trad" + "ing-abcd private migration"
    subprocess.run(["git", "-C", str(root), "commit", "-qm", private_message], check=True)
    private_ref = "refs/tags/" + "earnings" + "-predictions"
    subprocess.run(
        ["git", "-C", str(root), "tag", private_ref.removeprefix("refs/tags/")], check=True
    )

    findings = scan_git_history(root)
    assert any("copied issue identifier" in finding for finding in findings)
    assert any("private name" in finding for finding in findings)


def test_git_history_scan_timeouts_are_reported_as_findings(tmp_path: Path) -> None:
    with patch(
        "devtools.public_hygiene.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
    ):
        findings = scan_git_history(tmp_path)

    assert findings == [
        "git-refs: reachable Git metadata scan timed out",
        "git-commits: reachable Git metadata scan timed out",
    ]
