"""metaproc claude-auth — manage the Claude Code CLI Personal-Plan credential in Secret Manager.

Reads the Keychain-held OAuth credential on macOS via the ``security``
CLI and ships it to a per-user Secret Manager secret that Batch workers
bind at runtime. See
``docs/project/specs/active/plan-2026-04-19-claude-code-cli-personal-plan-auth.md``.

Zero plaintext on disk: the payload is read from the Keychain and piped
via stdin to ``gcloud secrets versions add --data-file=-``.
"""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from typing import Any, cast

import typer

from metaproc.cli import app, get_output
from metaproc.config.env_vars import MetaprocEnv

KEYCHAIN_SERVICE = "Claude Code-credentials"
SECRET_ACCESSOR_ROLE = "roles/secretmanager.secretAccessor"

claude_auth_app = typer.Typer(
    name="claude-auth",
    help="Manage the Claude Code Personal-Plan credential in GCP Secret Manager.",
    no_args_is_help=True,
)
app.add_typer(claude_auth_app)


# ── Helpers (all monkey-patchable for tests) ─────────────────────


def _require_darwin() -> None:
    if sys.platform != "darwin":
        msg = (
            "metaproc claude-auth requires macOS: the Personal-Plan "
            "credential is held in the login Keychain. Run this on the "
            "developer laptop that executed `claude login`."
        )
        raise typer.BadParameter(msg)


def _read_keychain_blob() -> str:
    """Return the Claude Code credentials Keychain blob as a UTF-8 string."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        msg = (
            f"Failed to read Keychain item {KEYCHAIN_SERVICE!r}: {stderr or exc}. "
            "Has `claude login` been run on this Mac?"
        )
        raise RuntimeError(msg) from exc
    # `security -w` appends a trailing newline; strip it so the secret
    # version payload matches the round-trip the worker will see.
    return result.stdout.rstrip("\n")


def _validate_creds_payload(payload: str) -> None:
    """Raise if *payload* is not a JSON object with a top-level ``claudeAiOauth`` key."""
    try:
        parsed: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        msg = f"Keychain payload is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(parsed, dict) or "claudeAiOauth" not in cast("dict[str, Any]", parsed):
        msg = (
            "Keychain payload is missing required top-level key 'claudeAiOauth'. "
            "Refusing to push a malformed credentials blob."
        )
        raise RuntimeError(msg)


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
    return f"claude-code-creds-{getpass.getuser()}"


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
    """Run ``gcloud`` with the given args; optionally send *stdin*.

    Indirection point for tests — monkey-patch this with a stub to stage
    fake gcloud output.
    """
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
    """Grant ``roles/secretmanager.secretAccessor`` on *name* to *service_account*.

    Idempotent: ``add-iam-policy-binding`` is a no-op when the binding
    already exists, so this is safe to call unconditionally. Phase 0
    confirmed that the Batch SA needs this grant explicitly — the
    default ``roles/editor`` does not include Secret Manager access.
    """
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
    """Push a new secret version from Keychain; create + grant IAM on first use."""
    out = get_output()
    _require_darwin()

    payload = _read_keychain_blob()
    _validate_creds_payload(payload)

    if _secret_exists(project, name):
        out.progress(f"Secret {name} already exists in project {project}.")
    else:
        out.progress(f"Creating secret {name} in project {project}...")
        _create_secret(project, name)

    out.progress(f"Granting {SECRET_ACCESSOR_ROLE} on {name} to {service_account} (idempotent)...")
    _grant_sa_access(project, name, service_account)

    out.progress(f"Adding new version to {name} from Keychain (via stdin)...")
    _add_version(project, name, payload)

    out.data(f"Pushed Claude Code credential to projects/{project}/secrets/{name}.")
    out.data(
        f"Set METAPROC_GCP_SECRET_CLAUDE_CREDS=projects/{project}/secrets/{name}/versions/latest"
    )


# ── Typer commands ───────────────────────────────────────────────


@claude_auth_app.command("push")
def push(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: claude-code-creds-<user>).",
    ),
) -> None:
    """Push the Keychain credential to Secret Manager (creating the secret + IAM grant if first use)."""
    proj = _resolve_project(project)
    name = _resolve_secret_name(secret_name)
    sa = _resolve_batch_sa()
    try:
        _push_core(proj, name, sa)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        get_output().error(str(exc))
        raise typer.Exit(code=1) from exc


@claude_auth_app.command("show")
def show(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: claude-code-creds-<user>).",
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


@claude_auth_app.command("rotate")
def rotate(
    project: str | None = typer.Option(  # noqa: UP007
        None, "--project", help="GCP project (default: $METAPROC_GCP_PROJECT)."
    ),
    secret_name: str | None = typer.Option(  # noqa: UP007
        None,
        "--secret-name",
        help="Secret name (default: claude-code-creds-<user>).",
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
        # First entry is the newest (just created); destroy the rest.
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
