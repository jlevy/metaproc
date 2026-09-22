"""Negative and positive contracts for the public hygiene gate."""

from __future__ import annotations

import gzip
import hashlib
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from devtools import public_hygiene
from devtools.public_hygiene import (
    _git_ignored,
    find_binary_findings,
    find_git_metadata_findings,
    find_hygiene_findings,
    repository_files,
    scan_file,
    scan_git_history,
)

# The gate exists to keep a private vocabulary out of this repository, so its own
# tests must not spell that vocabulary — not even split across string halves, which
# hides a name from the tokenizer but not from a reader. Every "private" token below
# is invented here and registered for the duration of one test, so the assertions
# exercise the real lookup without the file carrying anything real. Split literals
# remain only for the structural patterns (pull-request references, home paths,
# credentials, emails), which name nobody.
SYNTHETIC_PRIVATE_NAME = "synthetic-private-name"
SYNTHETIC_TREE_NAME = "synthetic-tree-only-name"
SYNTHETIC_ISSUE_PREFIX = "synthprefix"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def synthetic_private_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register invented tokens in each banned set for the duration of one test."""
    monkeypatch.setitem(
        public_hygiene.BANNED_TOKEN_HASHES, _digest(SYNTHETIC_PRIVATE_NAME), "private name"
    )
    monkeypatch.setitem(
        public_hygiene.BANNED_TREE_TOKEN_HASHES, _digest(SYNTHETIC_TREE_NAME), "private name"
    )
    monkeypatch.setattr(
        public_hygiene,
        "PRIVATE_ISSUE_PREFIX_HASHES",
        frozenset({_digest(SYNTHETIC_ISSUE_PREFIX)}),
    )


def test_public_metaproc_issue_tracking_language_is_allowed() -> None:
    assert find_hygiene_findings("AGENTS.md", "Track this as metaproc-abcd.") == []


def test_security_advisory_ids_are_not_pull_request_references() -> None:
    """A documented waiver has to be able to name the advisory it waives.

    GHSA identifiers are structured random strings, so a segment lands on the
    pull-request pattern by chance: the third group of `GHSA-g6cj-pr64-35w5`
    reads as a reference to a numbered pull request.
    """
    findings = find_hygiene_findings(
        "SUPPLY-CHAIN-SECURITY.md",
        "Waived: GHSA-g6cj-pr64-35w5 / CVE-2026-69247, unreachable from this closure.",
    )

    assert findings == []


def test_real_pull_request_references_are_still_rejected() -> None:
    """Masking advisory IDs must not blind the check to actual references."""
    # Split so this file does not trip the very check it exercises, matching
    # the convention above.
    hash_form = "PR #" + "4210"
    dash_form = "PR-" + "77"

    findings = find_hygiene_findings("NOTES.md", f"Fixed in {hash_form} and {dash_form}.")

    assert sum("private pull-request reference" in finding for finding in findings) == 2


def test_private_names_issue_ids_paths_and_credentials_are_rejected(
    synthetic_private_vocabulary: None,
) -> None:
    private_issue = f"{SYNTHETIC_ISSUE_PREFIX}-abcd"
    private_path = "/" + "Users/alice/work/repo"
    credential = "ghp_" + "a" * 32

    findings = find_hygiene_findings(
        f"fixtures/{SYNTHETIC_PRIVATE_NAME}/record.json",
        f"{private_issue}\n{private_path}\n{credential}",
    )

    assert any("private name" in finding for finding in findings)
    assert any("copied issue identifier" in finding for finding in findings)
    assert any("private home path" in finding for finding in findings)
    assert any("credential material" in finding for finding in findings)


def test_tree_tokens_are_rejected_in_files_but_not_in_reachable_git_metadata(
    synthetic_private_vocabulary: None,
) -> None:
    """The tree-only class is what lets a token be banned without rewriting history."""
    text = f"The {SYNTHETIC_TREE_NAME} pipeline drove this run."

    assert any(
        "private name" in finding for finding in find_hygiene_findings("docs/notes.md", text)
    )
    assert find_git_metadata_findings("git-commits", text) == []


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


def test_binary_assets_are_scanned_for_printable_private_residue(
    synthetic_private_vocabulary: None,
) -> None:
    private_name = SYNTHETIC_PRIVATE_NAME.encode()
    findings = find_binary_findings("static/image.bin", b"\x00\xff" + private_name + b"\x00")
    assert any("private name" in finding for finding in findings)


def test_zip_and_tar_members_are_scanned(
    tmp_path: Path, synthetic_private_vocabulary: None
) -> None:
    private_name = SYNTHETIC_PRIVATE_NAME
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


def test_nested_and_standalone_gzip_payloads_are_scanned(
    tmp_path: Path, synthetic_private_vocabulary: None
) -> None:
    private_name = SYNTHETIC_PRIVATE_NAME.encode()
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


def test_reachable_git_refs_and_commit_messages_are_scanned(
    tmp_path: Path, synthetic_private_vocabulary: None
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "public.txt").write_text("public")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    private_message = f"{SYNTHETIC_ISSUE_PREFIX}-abcd private migration"
    subprocess.run(["git", "-C", str(root), "commit", "-qm", private_message], check=True)
    private_ref = f"refs/tags/{SYNTHETIC_PRIVATE_NAME}"
    subprocess.run(
        ["git", "-C", str(root), "tag", private_ref.removeprefix("refs/tags/")], check=True
    )

    findings = scan_git_history(root)
    assert any("copied issue identifier" in finding for finding in findings)
    assert any("private name" in finding for finding in findings)


def test_unmerged_sibling_ref_is_not_part_of_current_history(
    tmp_path: Path, synthetic_private_vocabulary: None
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "public.txt").write_text("public")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "public commit"], check=True)

    sibling_ref = f"refs/remotes/origin/{SYNTHETIC_PRIVATE_NAME}"
    sibling_commit = subprocess.run(
        ["git", "-C", str(root), "commit-tree", "HEAD^{tree}", "-m", "sibling commit"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(root), "update-ref", sibling_ref, sibling_commit], check=True)

    findings = scan_git_history(root)

    assert not any("private name" in finding for finding in findings)


def test_branch_names_may_reference_a_pull_request(tmp_path: Path) -> None:
    """A branch named for a pull request is the same convention as its merge subject.

    Ref names also include every local branch, so scanning them strictly would fail the
    gate on one developer's checkout over a name that was never published.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "public.txt").write_text("public")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "public commit"], check=True)
    branch = "fix/pr-" + "19" + "-follow-up"
    subprocess.run(["git", "-C", str(root), "branch", branch], check=True)

    findings = scan_git_history(root)

    assert not any("private pull-request reference" in finding for finding in findings)


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
