"""Tests for cloud/gcp/dispatch_artifacts.py — wheel + workspace packaging."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metaproc.cloud.gcp.dispatch_artifacts import (
    build_wheel,
    find_metaproc_source_dir,
    package_workspace,
    upload_to_gcs,
    upload_wheel_to_gcs,
    upload_workspace_to_gcs,
)
from metaproc.io.digests import file_sha256, verify_file_sha256


def _git_init_with_files(repo: Path, files: dict[str, str], gitignore: str = "") -> None:
    """Init a git repo with files and an optional .gitignore, then commit.

    Defensively disables gpg/SSH signing for this repo. The fixture
    creates throwaway commits, and a globally-configured signing key
    (e.g. on a dev container with a remote signer) would otherwise
    make the commit fail with "signing operation failed".
    """
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "tag.gpgsign", "false"], check=True)
    if gitignore:
        (repo / ".gitignore").write_text(gitignore)
    for rel, content in files.items():
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)


# ── find_metaproc_source_dir ───────────────────────────────────


class TestFindMetaprocSourceDir:
    def test_locates_source_dir_from_module(self):
        src = find_metaproc_source_dir()
        assert (src / "pyproject.toml").exists()
        assert (src / "src" / "metaproc").is_dir()

    def test_raises_when_not_found(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Could not find metaproc source"):
            find_metaproc_source_dir(start=tmp_path / "nonexistent.py")


# ── build_wheel ───────────────────────────────────────────────


class TestBuildWheel:
    def test_produces_exactly_one_wheel(self, tmp_path: Path):
        src = find_metaproc_source_dir()
        wheel = build_wheel(source_dir=src, out_dir=tmp_path)
        assert wheel.suffix == ".whl"
        assert wheel.parent == tmp_path
        assert len(list(tmp_path.glob("*.whl"))) == 1
        assert wheel.name.startswith("metaproc-")

    def test_failure_raises(self, tmp_path: Path):
        bad_src = tmp_path / "no-such-source"
        bad_src.mkdir()
        with pytest.raises(RuntimeError, match="Failed to build wheel"):
            build_wheel(source_dir=bad_src, out_dir=tmp_path)


class TestArtifactDigests:
    def test_file_sha256_and_verification(self, tmp_path: Path) -> None:
        artifact = tmp_path / "artifact"
        artifact.write_bytes(b"artifact")
        expected = "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"

        assert file_sha256(artifact) == expected
        assert verify_file_sha256(artifact, expected, metadata_name="ARTIFACT_SHA256") == expected

    @pytest.mark.parametrize("digest", ["", "xyz", "0" * 64])
    def test_verification_rejects_missing_malformed_or_mismatched_digest(
        self, tmp_path: Path, digest: str
    ) -> None:
        artifact = tmp_path / "artifact"
        artifact.write_bytes(b"artifact")

        with pytest.raises(RuntimeError):
            verify_file_sha256(artifact, digest, metadata_name="ARTIFACT_SHA256")


# ── package_workspace ───────────────────────────────────────────


class TestPackageWorkspace:
    def test_default_includes_tracked_excludes_metaproc(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(
            repo,
            {
                "example_plugin/specs/foo.yaml": "x: 1",
                "metaproc/src/metaproc/__init__.py": "pass",
                "metaproc/pyproject.toml": "[project]\nname='metaproc'",
                "docs/readme.md": "hi",
            },
        )
        out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "example_plugin/specs/foo.yaml" in names
        assert "docs/readme.md" in names
        assert not any(n.startswith("metaproc/") for n in names)

    def test_default_excludes_vendored_metaproc_gitlink(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/keep.py": "pass"})
        nested = repo / "vendor" / "metaproc"
        _git_init_with_files(nested, {"src/metaproc/__init__.py": "pass"})
        nested_sha = subprocess.run(
            ["git", "-C", str(nested), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{nested_sha},vendor/metaproc",
            ],
            check=True,
        )

        out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")

        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/keep.py" in names
        assert not any(n.startswith("vendor/metaproc") for n in names)

    def test_excludes_gitignored_files(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(
            repo,
            {"src/keep.py": "pass"},
            gitignore="ignored.txt\n",
        )
        # Untracked file matching .gitignore shouldn't appear because it's
        # not in `git ls-files` output.
        (repo / "ignored.txt").write_text("nope")
        out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/keep.py" in names
        assert "ignored.txt" not in names

    def test_extra_paths_added(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/keep.py": "pass"})
        # Untracked file the caller wants shipped via --sync.
        (repo / "extra.json").write_text("{}")
        out = package_workspace(
            repo_root=repo,
            extra_paths=["extra.json"],
            out_path=tmp_path / "ws.tar.gz",
        )
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/keep.py" in names
        assert "extra.json" in names

    def test_sync_only_replaces_default(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(
            repo,
            {"src/a.py": "a", "src/b.py": "b", "docs/r.md": "r"},
        )
        out = package_workspace(
            repo_root=repo,
            sync_only=["src/a.py"],
            out_path=tmp_path / "ws.tar.gz",
        )
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/a.py" in names
        assert "src/b.py" not in names
        assert "docs/r.md" not in names

    def test_default_includes_untracked_non_gitignored(self, tmp_path: Path):
        # A brand-new file that is neither committed nor .gitignored should
        # still ship — iterating on a new spec before committing shouldn't
        # silently send stale data to the task.
        repo = tmp_path / "repo"
        _git_init_with_files(
            repo,
            {"src/tracked.py": "pass"},
            gitignore="ignored.txt\n",
        )
        (repo / "src" / "new.py").write_text("new")
        (repo / "ignored.txt").write_text("nope")
        out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/tracked.py" in names
        assert "src/new.py" in names
        assert "ignored.txt" not in names

    def test_default_skips_untracked_fifo_with_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/tracked.py": "pass"})
        fifo_path = repo / "agent.fifo"
        os.mkfifo(fifo_path)

        with (
            patch(
                "metaproc.cloud.gcp.dispatch_artifacts.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout="src/tracked.py\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="agent.fifo\n", stderr=""),
                ],
            ),
            caplog.at_level("WARNING"),
        ):
            out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")

        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/tracked.py" in names
        assert "agent.fifo" not in names
        assert any("agent.fifo" in record.message for record in caplog.records)

    @pytest.mark.parametrize("explicit_option", ["sync_only", "extra_paths"])
    def test_explicit_non_regular_workspace_path_is_rejected(
        self,
        tmp_path: Path,
        explicit_option: str,
    ) -> None:
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/tracked.py": "pass"})
        fifo_path = repo / "agent.fifo"
        os.mkfifo(fifo_path)

        with pytest.raises(ValueError, match="not a regular file or directory"):
            if explicit_option == "sync_only":
                package_workspace(
                    repo_root=repo,
                    out_path=tmp_path / "ws.tar.gz",
                    sync_only=["agent.fifo"],
                )
            else:
                package_workspace(
                    repo_root=repo,
                    out_path=tmp_path / "ws.tar.gz",
                    extra_paths=["agent.fifo"],
                )

    def test_sync_rejects_absolute_path(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/a.py": "a"})
        with pytest.raises(ValueError, match="absolute"):
            package_workspace(
                repo_root=repo,
                extra_paths=["/etc/passwd"],
                out_path=tmp_path / "ws.tar.gz",
            )

    def test_sync_rejects_escape_path(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/a.py": "a"})
        with pytest.raises(ValueError, match="escapes repo root"):
            package_workspace(
                repo_root=repo,
                extra_paths=["../outside.txt"],
                out_path=tmp_path / "ws.tar.gz",
            )

    def test_sync_only_rejects_escape_path(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/a.py": "a"})
        with pytest.raises(ValueError, match="escapes repo root"):
            package_workspace(
                repo_root=repo,
                sync_only=["../outside.txt"],
                out_path=tmp_path / "ws.tar.gz",
            )

    def test_skips_missing_path_with_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/a.py": "a"})
        with caplog.at_level("WARNING"):
            out = package_workspace(
                repo_root=repo,
                sync_only=["src/a.py", "src/missing.py"],
                out_path=tmp_path / "ws.tar.gz",
            )
        with tarfile.open(out) as tar:
            names = set(tar.getnames())
        assert "src/a.py" in names
        assert "src/missing.py" not in names
        assert any("missing.py" in r.message for r in caplog.records)

    def test_materializes_tracked_in_repo_symlink_as_regular_file(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"data/source.yaml": "answer: 42\n"})
        link = repo / "config" / "current.yaml"
        link.parent.mkdir()
        link.symlink_to("../data/source.yaml")
        subprocess.run(["git", "-C", str(repo), "add", "config/current.yaml"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "add link"],
            check=True,
        )

        out = package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")

        with tarfile.open(out) as tar:
            member = tar.getmember("config/current.yaml")
            extracted = tar.extractfile(member)
            assert member.isfile()
            assert extracted is not None
            assert extracted.read() == b"answer: 42\n"

    def test_sync_only_preserves_materialized_symlink_path(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"data/source.yaml": "answer: 42\n"})
        link = repo / "config" / "current.yaml"
        link.parent.mkdir()
        link.symlink_to("../data/source.yaml")

        out = package_workspace(
            repo_root=repo,
            sync_only=["config/current.yaml"],
            out_path=tmp_path / "ws.tar.gz",
        )

        with tarfile.open(out) as tar:
            assert tar.getnames() == ["config/current.yaml"]
            assert tar.getmember("config/current.yaml").isfile()

    def test_materializes_nested_in_repo_symlink_from_extra_directory(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(
            repo,
            {"data/source.yaml": "answer: 42\n"},
            gitignore="scratch/\n",
        )
        scratch = repo / "scratch"
        scratch.mkdir()
        (scratch / "current.yaml").symlink_to("../data/source.yaml")

        out = package_workspace(
            repo_root=repo,
            extra_paths=["scratch"],
            out_path=tmp_path / "ws.tar.gz",
        )

        with tarfile.open(out) as tar:
            member = tar.getmember("scratch/current.yaml")
            extracted = tar.extractfile(member)
            assert member.isfile()
            assert extracted is not None
            assert extracted.read() == b"answer: 42\n"

    def test_rejects_tracked_symlink_that_resolves_outside_repo(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"src/a.py": "a"})
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        leak = repo / "src" / "leak.txt"
        leak.symlink_to(outside)
        subprocess.run(["git", "-C", str(repo), "add", "src/leak.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "add link"],
            check=True,
        )

        with pytest.raises(ValueError, match="outside repo root"):
            package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")

    def test_rejects_directory_symlink_cycle(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _git_init_with_files(repo, {"assets/source.yaml": "answer: 42\n"})
        loop = repo / "assets" / "loop"
        loop.symlink_to(".", target_is_directory=True)
        subprocess.run(["git", "-C", str(repo), "add", "assets/loop"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "add loop"],
            check=True,
        )

        with pytest.raises(ValueError, match="directory link cycle"):
            package_workspace(repo_root=repo, out_path=tmp_path / "ws.tar.gz")


# ── upload helpers ───────────────────────────────────────────


class TestUploadToGcs:
    def test_uploads_via_storage_client(self, tmp_path: Path):
        local = tmp_path / "payload.txt"
        local.write_text("hi")

        bucket = MagicMock()
        blob = MagicMock()
        bucket.blob.return_value = blob
        client = MagicMock()
        client.bucket.return_value = bucket

        with patch(
            "metaproc.cloud.gcp.dispatch_artifacts.storage.Client",
            return_value=client,
        ) as client_factory:
            uri = upload_to_gcs(
                local,
                "gs://my-bucket/gcp-run/jobid/payload.txt",
                project="explicit-project",
            )

        assert uri == "gs://my-bucket/gcp-run/jobid/payload.txt"
        client_factory.assert_called_once_with(project="explicit-project")
        client.bucket.assert_called_once_with("my-bucket")
        bucket.blob.assert_called_once_with("gcp-run/jobid/payload.txt")
        blob.upload_from_filename.assert_called_once_with(str(local))
        assert blob.metadata == {"metaproc-sha256": file_sha256(local)}

    def test_rejects_non_gs_uri(self, tmp_path: Path):
        local = tmp_path / "x"
        local.write_text("")
        with pytest.raises(ValueError, match="Expected gs:// URI"):
            upload_to_gcs(local, "https://example.com/x", project="p")

    def test_rejects_uri_without_blob_path(self, tmp_path: Path):
        local = tmp_path / "x"
        local.write_text("")
        with pytest.raises(ValueError, match="Missing blob path"):
            upload_to_gcs(local, "gs://my-bucket", project="p")


class TestUploadWheelAndWorkspace:
    def test_wheel_uri_shape(self, tmp_path: Path):
        wheel = tmp_path / "metaproc-0.2.0-py3-none-any.whl"
        wheel.write_text("")

        client = MagicMock()
        bucket = MagicMock()
        blob = MagicMock()
        client.bucket.return_value = bucket
        bucket.blob.return_value = blob

        with patch(
            "metaproc.cloud.gcp.dispatch_artifacts.storage.Client",
            return_value=client,
        ):
            uri = upload_wheel_to_gcs(
                wheel,
                bucket="dispatch-bucket",
                job_id="job-abc-123",
                project="p",
            )

        assert uri == "gs://dispatch-bucket/gcp-run/job-abc-123/metaproc-0.2.0-py3-none-any.whl"
        bucket.blob.assert_called_once_with("gcp-run/job-abc-123/metaproc-0.2.0-py3-none-any.whl")

    def test_wheel_uri_distinct_job_ids_disjoint_keys(self, tmp_path: Path):
        """Two dispatches of the same wheel version must not overwrite each other."""
        wheel = tmp_path / "metaproc-0.2.0-py3-none-any.whl"
        wheel.write_text("")

        client = MagicMock()
        bucket = MagicMock()
        client.bucket.return_value = bucket

        with patch(
            "metaproc.cloud.gcp.dispatch_artifacts.storage.Client",
            return_value=client,
        ):
            uri_a = upload_wheel_to_gcs(wheel, bucket="b", job_id="job-a", project="p")
            uri_b = upload_wheel_to_gcs(wheel, bucket="b", job_id="job-b", project="p")

        assert uri_a == "gs://b/gcp-run/job-a/metaproc-0.2.0-py3-none-any.whl"
        assert uri_b == "gs://b/gcp-run/job-b/metaproc-0.2.0-py3-none-any.whl"
        assert uri_a != uri_b

    def test_workspace_uri_shape(self, tmp_path: Path):
        ws = tmp_path / "workspace.tar.gz"
        ws.write_text("")

        client = MagicMock()
        bucket = MagicMock()
        blob = MagicMock()
        client.bucket.return_value = bucket
        bucket.blob.return_value = blob

        with patch(
            "metaproc.cloud.gcp.dispatch_artifacts.storage.Client",
            return_value=client,
        ):
            uri = upload_workspace_to_gcs(
                ws,
                bucket="dispatch-bucket",
                job_id="job-abc-123",
                project="p",
            )

        assert uri == "gs://dispatch-bucket/gcp-run/job-abc-123/workspace.tar.gz"
        bucket.blob.assert_called_once_with("gcp-run/job-abc-123/workspace.tar.gz")

    def test_custom_prefix(self, tmp_path: Path):
        wheel = tmp_path / "w.whl"
        wheel.write_text("")
        client = MagicMock()
        bucket = MagicMock()
        client.bucket.return_value = bucket
        with patch(
            "metaproc.cloud.gcp.dispatch_artifacts.storage.Client",
            return_value=client,
        ):
            uri = upload_wheel_to_gcs(
                wheel,
                bucket="b",
                job_id="j1",
                project="p",
                prefix="custom-prefix",
            )
        assert uri == "gs://b/custom-prefix/j1/w.whl"
