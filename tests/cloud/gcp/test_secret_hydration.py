"""Secret Manager references cross Batch; plaintext credentials do not."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from metaproc.cloud.gcp import secret_hydration


def test_attach_secret_refs_serializes_only_resource_names() -> None:
    env = {"VISIBLE": "value"}
    refs = {
        "SERPAPI_API_KEY": "projects/p/secrets/serpapi/versions/latest",
        "GH_TOKEN": "projects/p/secrets/github/versions/3",
    }

    secret_hydration.attach_secret_refs(env, refs)

    assert env["VISIBLE"] == "value"
    assert json.loads(env[secret_hydration.SECRET_REFS_ENV]) == refs
    assert "SERPAPI_API_KEY" not in env
    assert "GH_TOKEN" not in env


def test_secret_refs_require_explicit_runtime_identity() -> None:
    refs = {"TOKEN": "projects/p/secrets/token/versions/1"}

    with pytest.raises(ValueError, match="METAPROC_GCP_SERVICE_ACCOUNT"):
        secret_hydration.require_secret_service_account(refs, "")

    secret_hydration.require_secret_service_account(refs, "runner@example.invalid")
    secret_hydration.require_secret_service_account({}, "")


def test_attach_secret_refs_refuses_plaintext_target_in_batch_env() -> None:
    env = {"TOKEN": "plaintext"}

    with pytest.raises(ValueError, match="secret target already exists"):
        secret_hydration.attach_secret_refs(
            env,
            {"TOKEN": "projects/p/secrets/token/versions/1"},
        )

    assert env == {"TOKEN": "plaintext"}


def test_attach_secret_refs_refuses_self_target() -> None:
    with pytest.raises(ValueError, match="cannot target its own"):
        secret_hydration.attach_secret_refs(
            {},
            {secret_hydration.SECRET_REFS_ENV: ("projects/p/secrets/contract/versions/1")},
        )


def test_hydrate_secret_env_fetches_all_values_before_mutating(monkeypatch) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {
                "FIRST_TOKEN": "projects/p/secrets/first/versions/1",
                "SECOND_TOKEN": "projects/p/secrets/second/versions/latest",
            }
        )
    }
    client = MagicMock()
    client.access_secret_version.side_effect = [
        SimpleNamespace(payload=SimpleNamespace(data=b"first-value")),
        SimpleNamespace(payload=SimpleNamespace(data=b"second-value")),
    ]
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", lambda: client)

    hydrated = secret_hydration.hydrate_secret_env(env)

    assert hydrated == ("FIRST_TOKEN", "SECOND_TOKEN")
    assert env["FIRST_TOKEN"] == "first-value"
    assert env["SECOND_TOKEN"] == "second-value"
    assert [
        call.kwargs["request"]["name"] for call in client.access_secret_version.call_args_list
    ] == [
        "projects/p/secrets/first/versions/1",
        "projects/p/secrets/second/versions/latest",
    ]
    client.close.assert_called_once_with()


def test_hydrate_secret_env_leaves_env_unchanged_when_fetch_fails(monkeypatch) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {
                "FIRST_TOKEN": "projects/p/secrets/first/versions/1",
                "SECOND_TOKEN": "projects/p/secrets/second/versions/1",
            }
        )
    }
    before = dict(env)
    client = MagicMock()
    client.access_secret_version.side_effect = [
        SimpleNamespace(payload=SimpleNamespace(data=b"first-value")),
        RuntimeError("denied"),
    ]
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", lambda: client)

    with pytest.raises(
        RuntimeError,
        match="failed to hydrate secret env var 'SECOND_TOKEN'",
    ) as exc:
        secret_hydration.hydrate_secret_env(env)

    assert "RuntimeError" in str(exc.value)
    assert "denied" not in str(exc.value)
    assert env == before
    client.close.assert_called_once_with()


def test_hydrate_secret_env_reports_client_initialization_without_provider_detail(
    monkeypatch,
) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {"API_TOKEN": "projects/p/secrets/api/versions/1"}
        )
    }
    monkeypatch.setattr(
        secret_hydration,
        "_new_secret_manager_client",
        MagicMock(side_effect=RuntimeError("credential body")),
    )

    with pytest.raises(RuntimeError, match="failed to initialize Secret Manager client") as exc:
        secret_hydration.hydrate_secret_env(env)
    assert "RuntimeError" in str(exc.value)
    assert "credential body" not in str(exc.value)


def test_hydrate_secret_env_reports_missing_gcp_extra(monkeypatch) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {"API_TOKEN": "projects/p/secrets/api/versions/1"}
        )
    }
    monkeypatch.setattr(
        secret_hydration,
        "_new_secret_manager_client",
        MagicMock(side_effect=ImportError("provider import body")),
    )

    with pytest.raises(RuntimeError, match=r"metaproc\[gcp-batch\].*agent image") as exc:
        secret_hydration.hydrate_secret_env(env)
    assert "ImportError" in str(exc.value)
    assert "provider import body" not in str(exc.value)


def test_hydrate_secret_env_retries_transient_client_and_fetch_failures(monkeypatch) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {"API_TOKEN": "projects/p/secrets/api/versions/1"}
        )
    }
    client = MagicMock()
    client.access_secret_version.side_effect = [
        RuntimeError("deadline exceeded"),
        SimpleNamespace(payload=SimpleNamespace(data=b"value")),
    ]
    factory = MagicMock(side_effect=[RuntimeError("metadata transport unavailable"), client])
    sleep = MagicMock()
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", factory)
    monkeypatch.setattr("metaproc.cloud.gcp.secret_hydration.time.sleep", sleep)

    assert secret_hydration.hydrate_secret_env(env) == ("API_TOKEN",)
    assert env["API_TOKEN"] == "value"
    assert factory.call_count == 2
    assert client.access_secret_version.call_count == 2
    assert sleep.call_count == 2


def test_hydrate_secret_env_records_close_failure_without_masking_success(
    monkeypatch,
    caplog,
) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {"API_TOKEN": "projects/p/secrets/api/versions/1"}
        )
    }
    client = MagicMock()
    client.access_secret_version.return_value = SimpleNamespace(
        payload=SimpleNamespace(data=b"value")
    )
    client.close.side_effect = RuntimeError("close body")
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", lambda: client)

    with caplog.at_level(logging.DEBUG, logger=secret_hydration.__name__):
        assert secret_hydration.hydrate_secret_env(env) == ("API_TOKEN",)

    assert "Secret Manager client close failed (RuntimeError)" in caplog.text
    assert "close body" not in caplog.text


def test_hydrate_secret_env_skips_client_for_empty_mapping(monkeypatch) -> None:
    client_factory = MagicMock()
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", client_factory)

    assert secret_hydration.hydrate_secret_env({secret_hydration.SECRET_REFS_ENV: "{}"}) == ()
    client_factory.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "JSON object"),
        (json.dumps({"bad-name": "projects/p/secrets/s/versions/1"}), "env var name"),
        (json.dumps({"TOKEN": "not-a-secret-ref"}), "Secret Manager version"),
    ],
)
def test_hydrate_secret_env_rejects_malformed_dispatch_contract(raw: str, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        secret_hydration.hydrate_secret_env({secret_hydration.SECRET_REFS_ENV: raw})


def test_hydrate_secret_env_refuses_ambient_plaintext(monkeypatch) -> None:
    env = {
        secret_hydration.SECRET_REFS_ENV: json.dumps(
            {"TOKEN": "projects/p/secrets/token/versions/1"}
        ),
        "TOKEN": "ambient-value",
    }
    factory = MagicMock()
    monkeypatch.setattr(secret_hydration, "_new_secret_manager_client", factory)

    with pytest.raises(RuntimeError, match="already exists"):
        secret_hydration.hydrate_secret_env(env)

    factory.assert_not_called()
