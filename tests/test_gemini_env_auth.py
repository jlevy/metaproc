"""Gemini authentication from the launch environment, in the order gemini-cli reads it.

Under the default private home the settings name no auth type, so gemini-cli falls back
to `getAuthTypeFromEnv`, which checks `GOOGLE_GENAI_USE_GCA`, then
`GOOGLE_GENAI_USE_VERTEXAI`, then `GOOGLE_GEMINI_BASE_URL`, then `GEMINI_API_KEY`, each
`=== "true"` or non-empty. Login with Google then needs credentials the private home does
not hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter

_AUTH_VARIABLES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_GENAI_USE_GCA",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_CLOUD_ACCESS_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE",
)


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("metaproc.adapters.gemini_cli.shutil.which", lambda _name: "/bin/gemini")
    return monkeypatch


@pytest.mark.parametrize(
    ("environment", "auth_mode", "ready"),
    [
        ({"GOOGLE_GENAI_USE_GCA": "true", "GEMINI_API_KEY": "example"}, "oauth-personal", False),
        (
            {"GOOGLE_GENAI_USE_GCA": "true", "GOOGLE_CLOUD_ACCESS_TOKEN": "example"},
            "oauth-personal",
            True,
        ),
        ({"GOOGLE_GENAI_USE_GCA": "1", "GEMINI_API_KEY": "example"}, "gemini-api-key", True),
        (
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "example-project",
                "GEMINI_API_KEY": "example",
            },
            "vertex-ai",
            True,
        ),
        (
            {"GOOGLE_GEMINI_BASE_URL": "https://gateway.example", "GEMINI_API_KEY": "example"},
            "gateway",
            True,
        ),
        ({"GEMINI_API_KEY": "example"}, "gemini-api-key", True),
    ],
)
def test_check_auth_reports_the_mode_the_cli_selects(
    auth_env: pytest.MonkeyPatch,
    environment: dict[str, str],
    auth_mode: str,
    ready: bool,
) -> None:
    for name, value in environment.items():
        auth_env.setenv(name, value)

    status = GeminiCliAdapter().check_auth()

    assert (status.auth_mode, status.credentials_found) == (auth_mode, ready)


def test_check_auth_explains_an_unusable_login(auth_env: pytest.MonkeyPatch) -> None:
    auth_env.setenv("GOOGLE_GENAI_USE_GCA", "true")
    auth_env.setenv("GEMINI_API_KEY", "example")

    status = GeminiCliAdapter().check_auth()

    assert "GOOGLE_GENAI_USE_GCA" in status.details
    assert "native_settings_home_template" in status.setup_hint
    assert "workspace" in status.setup_hint


def test_prepare_env_refuses_a_login_the_private_home_cannot_hold(tmp_path: Path) -> None:
    """The CLI would pick Login with Google over the API key and find no credentials."""
    environment = {
        "HOME": str(tmp_path),
        "GOOGLE_GENAI_USE_GCA": "true",
        "GEMINI_API_KEY": "example",
    }

    with pytest.raises(ValueError, match="GOOGLE_GENAI_USE_GCA") as refusal:
        GeminiCliAdapter().prepare_env(environment, {"working_directory": str(tmp_path)})

    assert "native_settings_home_template" in str(refusal.value)


@pytest.mark.parametrize(
    "login",
    [
        {"GOOGLE_CLOUD_ACCESS_TOKEN": "example"},
        {"GOOGLE_APPLICATION_CREDENTIALS": "/example/credentials.json"},
        {"GEMINI_FORCE_ENCRYPTED_FILE_STORAGE": "true"},
    ],
)
def test_prepare_env_accepts_a_login_from_outside_the_home(
    tmp_path: Path, login: dict[str, str]
) -> None:
    environment = {"HOME": str(tmp_path), "GOOGLE_GENAI_USE_GCA": "true", **login}

    env = GeminiCliAdapter().prepare_env(environment, {"working_directory": str(tmp_path)})

    assert env["GEMINI_CLI_HOME"]


def test_prepare_env_accepts_a_login_carried_by_a_template(tmp_path: Path) -> None:
    template = tmp_path / "template"
    (template / ".gemini").mkdir(parents=True)
    (template / ".gemini" / "settings.json").write_text("{}", encoding="utf-8")
    (template / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    environment = {"HOME": str(tmp_path), "GOOGLE_GENAI_USE_GCA": "true"}
    config: dict[str, object] = {
        "working_directory": str(tmp_path),
        "native_settings_home_template": str(template),
    }

    env = GeminiCliAdapter().prepare_env(environment, config)

    assert (Path(env["GEMINI_CLI_HOME"]) / ".gemini" / "oauth_creds.json").is_file()


def test_prepare_env_accepts_settings_that_name_an_auth_type(tmp_path: Path) -> None:
    """A configured auth type takes precedence over the environment fallback."""
    environment = {"HOME": str(tmp_path), "GOOGLE_GENAI_USE_GCA": "true"}
    config: dict[str, object] = {
        "working_directory": str(tmp_path),
        "native_settings": {"security": {"auth": {"selectedType": "vertex-ai"}}},
    }
    GeminiCliAdapter().prepare_env(environment, config)

    workspace = tmp_path / "task"
    (workspace / ".gemini").mkdir(parents=True)
    (workspace / ".gemini" / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "vertex-ai"}}}), encoding="utf-8"
    )
    GeminiCliAdapter().prepare_env(environment, {"working_directory": str(workspace)})


def test_workspace_scope_keeps_the_operator_login(tmp_path: Path) -> None:
    environment = {"HOME": str(tmp_path), "GOOGLE_GENAI_USE_GCA": "true"}
    config: dict[str, object] = {
        "native_settings_scope": "workspace",
        "working_directory": str(tmp_path / "task"),
    }

    env = GeminiCliAdapter().prepare_env(environment, config)

    assert "GEMINI_CLI_HOME" not in env
