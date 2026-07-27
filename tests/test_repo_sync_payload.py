"""Tests for the RepoSyncPayload typed wrapper.

Phase 10 follow-up of the Vehicle A pool redesign.
"""

from __future__ import annotations

from metaproc.dispatch.repo_sync_payload import RepoSyncPayload


class TestEnvVarNames:
    def test_class_var_names_match_metaproc_env_dot_name(self) -> None:
        assert RepoSyncPayload.ENV_REPO_URL == "METAPROC_REPO_URL"
        assert RepoSyncPayload.ENV_RUN_BRANCH == "METAPROC_RUN_BRANCH"
        assert RepoSyncPayload.ENV_WHEEL_GCS == "METAPROC_WHEEL_GCS"
        assert RepoSyncPayload.ENV_WHEEL_SHA256 == "METAPROC_WHEEL_SHA256"
        assert RepoSyncPayload.ENV_WORKSPACE_GCS == "METAPROC_WORKSPACE_GCS"
        assert RepoSyncPayload.ENV_WORKSPACE_SHA256 == "METAPROC_WORKSPACE_SHA256"
        assert RepoSyncPayload.ENV_SPARSE_PATHS == "METAPROC_REPO_SPARSE_PATHS"
        assert RepoSyncPayload.ENV_WORKSPACE_PACKAGES == "METAPROC_WORKSPACE_PACKAGES"
        assert RepoSyncPayload.ENV_BUNDLED_REPO_DIR == "METAPROC_BUNDLED_REPO_DIR"

    def test_all_env_var_names_in_declaration_order(self) -> None:
        assert RepoSyncPayload.all_env_var_names() == (
            "METAPROC_REPO_URL",
            "METAPROC_RUN_BRANCH",
            "METAPROC_WHEEL_GCS",
            "METAPROC_WHEEL_SHA256",
            "METAPROC_WORKSPACE_GCS",
            "METAPROC_WORKSPACE_SHA256",
            "METAPROC_REPO_SPARSE_PATHS",
            "METAPROC_WORKSPACE_PACKAGES",
            "METAPROC_BUNDLED_REPO_DIR",
        )


class TestFromEnv:
    def test_empty_mapping_returns_default(self) -> None:
        assert RepoSyncPayload.from_env({}) == RepoSyncPayload()

    def test_full_payload(self) -> None:
        p = RepoSyncPayload.from_env(
            {
                "METAPROC_REPO_URL": "https://github.com/x/y.git",
                "METAPROC_RUN_BRANCH": "feature-1",
                "METAPROC_WHEEL_GCS": "gs://b/wheel.whl",
                "METAPROC_WHEEL_SHA256": "1" * 64,
                "METAPROC_WORKSPACE_GCS": "gs://b/ws.tar.gz",
                "METAPROC_WORKSPACE_SHA256": "2" * 64,
                "METAPROC_REPO_SPARSE_PATHS": "plugin,fixtures",
                "METAPROC_WORKSPACE_PACKAGES": "plugin",
                "METAPROC_BUNDLED_REPO_DIR": "/opt/workspace",
            }
        )
        assert p.repo_url == "https://github.com/x/y.git"
        assert p.run_branch == "feature-1"
        assert p.wheel_gcs == "gs://b/wheel.whl"
        assert p.wheel_sha256 == "1" * 64
        assert p.workspace_gcs == "gs://b/ws.tar.gz"
        assert p.workspace_sha256 == "2" * 64
        assert p.sparse_paths == "plugin,fixtures"
        assert p.workspace_packages == "plugin"
        assert p.bundled_repo_dir == "/opt/workspace"

    def test_gcs_uris_stripped(self) -> None:
        # Operators sometimes paste gs:// URIs with stray newlines —
        # the strip handles that.
        p = RepoSyncPayload.from_env(
            {
                "METAPROC_WHEEL_GCS": "  gs://b/wheel.whl\n",
                "METAPROC_WORKSPACE_GCS": " gs://b/ws.tar.gz\t",
            }
        )
        assert p.wheel_gcs == "gs://b/wheel.whl"
        assert p.workspace_gcs == "gs://b/ws.tar.gz"


class TestToEnvVars:
    def test_default_emits_empty_dict(self) -> None:
        assert RepoSyncPayload().to_env_vars() == {}

    def test_full_payload(self) -> None:
        p = RepoSyncPayload(
            repo_url="https://github.com/x/y.git",
            run_branch="feature-1",
            wheel_gcs="gs://b/wheel.whl",
            wheel_sha256="1" * 64,
            workspace_gcs="gs://b/ws.tar.gz",
            workspace_sha256="2" * 64,
            sparse_paths="plugin,fixtures",
            workspace_packages="plugin",
            bundled_repo_dir="/opt/workspace",
        )
        assert p.to_env_vars() == {
            "METAPROC_REPO_URL": "https://github.com/x/y.git",
            "METAPROC_RUN_BRANCH": "feature-1",
            "METAPROC_WHEEL_GCS": "gs://b/wheel.whl",
            "METAPROC_WHEEL_SHA256": "1" * 64,
            "METAPROC_WORKSPACE_GCS": "gs://b/ws.tar.gz",
            "METAPROC_WORKSPACE_SHA256": "2" * 64,
            "METAPROC_REPO_SPARSE_PATHS": "plugin,fixtures",
            "METAPROC_WORKSPACE_PACKAGES": "plugin",
            "METAPROC_BUNDLED_REPO_DIR": "/opt/workspace",
        }

    def test_partial_payload_omits_unset(self) -> None:
        p = RepoSyncPayload(repo_url="https://github.com/x/y.git")
        result = p.to_env_vars()
        assert result == {"METAPROC_REPO_URL": "https://github.com/x/y.git"}


class TestRoundTrip:
    def test_default_round_trips(self) -> None:
        p = RepoSyncPayload()
        assert RepoSyncPayload.from_env(p.to_env_vars()) == p

    def test_full_round_trips(self) -> None:
        p = RepoSyncPayload(
            repo_url="https://github.com/x/y.git",
            run_branch="b",
            wheel_gcs="gs://w",
            wheel_sha256="1" * 64,
            workspace_gcs="gs://ws",
            workspace_sha256="2" * 64,
            sparse_paths="plugin,fixtures",
            workspace_packages="plugin",
            bundled_repo_dir="/opt/workspace",
        )
        assert RepoSyncPayload.from_env(p.to_env_vars()) == p
