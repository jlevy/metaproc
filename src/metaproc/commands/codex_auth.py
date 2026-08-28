"""metaproc codex-auth — manage the Codex CLI ChatGPT-OAuth credential in Secret Manager.

Reads ``~/.codex/auth.json`` (the file-on-disk OAuth blob managed by
``codex login``) and ships it to a per-user Secret Manager secret that
Batch workers bind at runtime. Phase 2 of the codex-adapter spec; see
``src/metaproc/docs/metaproc-design.md``
and the auth research brief at
``src/metaproc/docs/metaproc-design.md``.

Zero plaintext on disk: the payload is piped from the auth.json blob
via stdin to ``gcloud secrets versions add --data-file=-``.

Distinct from claude-auth: codex stores its OAuth blob as a plain file
(``~/.codex/auth.json``) rather than in macOS Keychain, so push works
cross-platform — but only if ``cli_auth_credentials_store = "file"`` is
set in ``~/.codex/config.toml``. Without that setting, codex-cli's
default (``"auto"``) may land creds in the OS credential store instead,
yielding no file to push. The pre-push preflight enforces this.
"""

from __future__ import annotations

import getpass
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any, cast

import typer

from metaproc.adapters.codex import resolve_codex_home
from metaproc.cli import app, get_output
from metaproc.config.env_vars import MetaprocEnv

SECRET_ACCESSOR_ROLE = "roles/secretmanager.secretAccessor"

codex_auth_app = typer.Typer(
    name="codex-auth",
    help="Manage the Codex CLI ChatGPT-OAuth credential in GCP Secret Manager.",
    no_args_is_help=True,
)
app.add_typer(codex_auth_app)


# ── Path helpers ─────────────────────────────────────────────────


def _codex_home() -> Path:
    """Return the effective codex-cli home directory.

    Delegates to the shared ``resolve_codex_home`` helper so this command
    and the codex adapter agree on which directory holds the session.
    """
    return resolve_codex_home()


def _auth_json_path() -> Path:
    return _codex_home() / "auth.json"


def _config_toml_path() -> Path:
    return _codex_home() / "config.toml"


# ── Preflight ─────────────────────────────────────


def _preflight_check_config_store() -> None:
    """Fail-hard if ``cli_auth_credentials_store`` is not ``"file"`` in config.toml.

    codex-cli's default is ``"auto"``, which may land credentials in the
    macOS Keychain or Windows Credential Manager instead of
    ``~/.codex/auth.json``. Without this preflight, ``codex-auth push``
    appears to succeed on some laptops and fails with "no such file" on
    others. See research §F4 and §F10 for the full vehicle taxonomy.
    """
    config_path = _config_toml_path()
    if not config_path.is_file():
        msg = (
            f"{config_path} does not exist. codex-cli's default "
            '`cli_auth_credentials_store = "auto"` may land credentials in the '
            "OS credential store instead of ~/.codex/auth.json, which would "
            "leave codex-auth push with nothing to push.\n"
            "Fix:\n"
            f"  1. mkdir -p {config_path.parent}\n"
            f"  2. echo 'cli_auth_credentials_store = \"file\"' > {config_path}\n"
            "  3. Re-run `codex login` (or rotate the existing session) so "
            "credentials land in ~/.codex/auth.json.\n"
            "  4. Re-run `metaproc codex-auth push`."
        )
        raise RuntimeError(msg)

    text = config_path.read_text()
    store_value = _parse_toml_string_value(text, "cli_auth_credentials_store")
    if store_value != "file":
        current = store_value if store_value is not None else "(unset — defaults to auto)"
        msg = (
            f"{config_path} has cli_auth_credentials_store = {current!r}, "
            'but codex-auth push requires it to be explicitly set to "file".\n'
            "Without this setting, codex-cli may land credentials in the OS credential "
            "store (macOS Keychain / Windows Credential Manager) instead of "
            "~/.codex/auth.json, yielding no file to push.\n"
            "Fix:\n"
            f'  1. Edit {config_path} and set `cli_auth_credentials_store = "file"`\n'
            "  2. Re-run `codex login` so the credentials re-materialize to "
            "~/.codex/auth.json.\n"
            "  3. Re-run `metaproc codex-auth push`."
        )
        raise RuntimeError(msg)


def _parse_toml_string_value(text: str, key: str) -> str | None:
    """Return the top-level (root-table) string value for *key*, or None.

    Uses stdlib ``tomllib`` so TOML edge cases (table headers, multi-line
    strings, inline comments inside values, literal vs basic strings) are
    handled correctly. Returns None on parse failure or when the key is
    missing / not at root scope.
    """

    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    value = doc.get(key)
    return value if isinstance(value, str) else None


# ── Blob read + validate ─────────────────────────────────────────


def _read_auth_json_blob() -> str:
    """Return the contents of ~/.codex/auth.json as a UTF-8 string."""
    path = _auth_json_path()
    if not path.is_file():
        msg = (
            f"{path} does not exist. Run `codex login` on this machine to "
            "materialize the ChatGPT-OAuth session to disk. If login ran but "
            "the file still does not exist, check that "
            'cli_auth_credentials_store = "file" is set in '
            f"{_config_toml_path()}."
        )
        raise RuntimeError(msg)
    return path.read_text()


def _validate_codex_payload(payload: str) -> None:
    """Raise if payload is not a JSON object with a ChatGPT-OAuth tokens block."""
    try:
        parsed: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        msg = f"{_auth_json_path()} is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"{_auth_json_path()} payload must be a JSON object"
        raise RuntimeError(msg)
    payload_dict = cast("dict[str, Any]", parsed)
    tokens = payload_dict.get("tokens")
    if not isinstance(tokens, dict):
        msg = (
            f"{_auth_json_path()} is missing the required top-level 'tokens' block. "
            "codex-auth push only accepts ChatGPT-OAuth sessions (use `codex login`)."
        )
        raise RuntimeError(msg)
    auth_mode = cast("dict[str, Any]", tokens).get("auth_mode")
    if auth_mode != "chatgpt":
        msg = (
            f"{_auth_json_path()} has tokens.auth_mode={auth_mode!r}. "
            "codex-auth push only supports ChatGPT-OAuth sessions (auth_mode=chatgpt). "
            "API-key auth should be exported as OPENAI_API_KEY directly."
        )
        raise RuntimeError(msg)


# ── gcloud + Secret Manager helpers (monkey-patchable for tests) ─


def _resolve_project(project: str | None) -> str:
    if project:
        return project
    env_project = MetaprocEnv.METAPROC_GCP_PROJECT.read_str(default="")
    if not env_project:
        msg = (
            "--project not given and METAPROC_GCP_PROJECT is not set. "
            "Pass --project or export METAPROC_GCP_PROJECT."
        )
        raise typer.BadParameter(msg)
    return env_project


def _resolve_secret_name(secret_name: str | None) -> str:
    if secret_name:
        return secret_name
    return f"codex-creds-{getpass.getuser()}"


def _resolve_batch_sa() -> str:
    service_account = MetaprocEnv.METAPROC_GCP_SERVICE_ACCOUNT.read_str(default="")
    if not service_account:
        raise typer.BadParameter(
            "METAPROC_GCP_SERVICE_ACCOUNT is required; refusing to grant a default principal"
        )
    return service_account


def _gcloud(
    args: list[str],
    *,
    stdin: str | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run gcloud; optionally send stdin. Indirection point for tests."""
    return subprocess.run(
        ["gcloud", *args],
        check=True,
        input=stdin,
        capture_output=capture,
        text=True,
    )


def _secret_exists(project: str, name: str) -> bool:
    try:
        _gcloud(
            ["secrets", "describe", name, f"--project={project}", "--format=value(name)"],
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _create_secret(project: str, name: str) -> None:
    _gcloud(
        [
            "secrets",
            "create",
            name,
            f"--project={project}",
            "--replication-policy=automatic",
        ],
    )


def _grant_sa_access(project: str, name: str, service_account: str) -> None:
    """Grant roles/secretmanager.secretAccessor to *service_account*. Idempotent."""
    _gcloud(
        [
            "secrets",
            "add-iam-policy-binding",
            name,
            f"--project={project}",
            f"--member=serviceAccount:{service_account}",
            f"--role={SECRET_ACCESSOR_ROLE}",
        ],
    )


def _add_version(project: str, name: str, payload: str) -> None:
    _gcloud(
        [
            "secrets",
            "versions",
            "add",
            name,
            f"--project={project}",
            "--data-file=-",
        ],
        stdin=payload,
    )


def _list_enabled_versions(project: str, name: str) -> list[str]:
    """Return enabled version numbers (as strings), newest first."""
    result = _gcloud(
        [
            "secrets",
            "versions",
            "list",
            name,
            f"--project={project}",
            "--filter=state:ENABLED",
            "--sort-by=~createTime",
            "--format=value(name)",
        ],
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _destroy_version(project: str, name: str, version: str) -> None:
    _gcloud(
        [
            "secrets",
            "versions",
            "destroy",
            version,
            f"--secret={name}",
            f"--project={project}",
            "--quiet",
        ],
    )


def _describe_metadata(project: str, name: str) -> str:
    result = _gcloud(
        [
            "secrets",
            "describe",
            name,
            f"--project={project}",
            "--format=yaml(name,createTime,replication)",
        ],
    )
    return result.stdout


def _get_iam_policy(project: str, name: str) -> str:
    result = _gcloud(
        [
            "secrets",
            "get-iam-policy",
            name,
            f"--project={project}",
            "--format=yaml",
        ],
    )
    return result.stdout


# ── Core flow ────────────────────────────────────────────────────


def _push_core(project: str, name: str, service_account: str) -> None:
    """Push a new secret version from ~/.codex/auth.json."""
    out = get_output()

    _preflight_check_config_store()
    payload = _read_auth_json_blob()
    _validate_codex_payload(payload)

    if _secret_exists(project, name):
        out.progress(f"Secret {name} already exists in project {project}.")
    else:
        out.progress(f"Creating secret {name} in project {project}...")
        _create_secret(project, name)

    out.progress(f"Granting {SECRET_ACCESSOR_ROLE} on {name} to {service_account} (idempotent)...")
    _grant_sa_access(project, name, service_account)

    out.progress(f"Adding new version to {name} from ~/.codex/auth.json (via stdin)...")
    _add_version(project, name, payload)

    out.data(f"Pushed Codex ChatGPT-OAuth credential to projects/{project}/secrets/{name}.")
    out.data(
        f"Set METAPROC_GCP_SECRET_CODEX_CREDS=projects/{project}/secrets/{name}/versions/latest"
    )


# ── Typer commands ───────────────────────────────────────────────


@codex_auth_app.command("push")
def push(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: codex-creds-<user>).",
    ),
) -> None:
    """Push ~/.codex/auth.json to Secret Manager (creating the secret + IAM grant if first use)."""
    proj = _resolve_project(project)
    name = _resolve_secret_name(secret_name)
    sa = _resolve_batch_sa()
    try:
        _push_core(proj, name, sa)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        get_output().error(str(exc))
        raise typer.Exit(code=1) from exc


@codex_auth_app.command("show")
def show(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: codex-creds-<user>).",
    ),
) -> None:
    """Show Secret Manager metadata and IAM policy for the credential secret. Never prints the payload."""
    out = get_output()
    proj = _resolve_project(project)
    name = _resolve_secret_name(secret_name)
    try:
        metadata = _describe_metadata(proj, name)
        policy = _get_iam_policy(proj, name)
    except subprocess.CalledProcessError as exc:
        out.error(f"gcloud failed: {(exc.stderr or '').strip() or exc}")
        raise typer.Exit(code=1) from exc
    out.data(f"# projects/{proj}/secrets/{name} — metadata")
    out.data(metadata)
    out.data(f"# projects/{proj}/secrets/{name} — IAM policy")
    out.data(policy)


@codex_auth_app.command("rotate")
def rotate(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: codex-creds-<user>).",
    ),
) -> None:
    """Push a new version, then destroy prior enabled versions (keeping only the latest)."""
    out = get_output()
    proj = _resolve_project(project)
    name = _resolve_secret_name(secret_name)
    sa = _resolve_batch_sa()
    try:
        _push_core(proj, name, sa)
        enabled = _list_enabled_versions(proj, name)
        stale = enabled[1:]
        if not stale:
            out.data("No prior enabled versions to destroy.")
            return
        for version in stale:
            out.progress(f"Destroying prior enabled version {version}...")
            _destroy_version(proj, name, version)
        out.data(f"Rotated {name}: kept version {enabled[0]}, destroyed {len(stale)} prior.")
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        out.error(str(exc))
        raise typer.Exit(code=1) from exc
