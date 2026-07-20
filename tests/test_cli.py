"""Tests for the Directory CLI (token-paste mode).

The tests patch `_build_client` to return a client with an httpx.MockTransport, so
nothing hits the network.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from directory_cli import auth as auth_mod
from directory_cli import client as client_mod
from directory_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_cached_token(monkeypatch):
    """Default: no keyring token, so tests never touch the real OS keyring."""
    monkeypatch.setattr(auth_mod, "get_id_token", lambda config: None)


@pytest.fixture
def patch_client(monkeypatch):
    """Return a factory that installs a mock transport and yields the captured requests."""

    def _apply(status: int = 200, json_body=None, content=None, headers=None):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if content is not None:
                return httpx.Response(status, content=content, headers=headers or {})
            return httpx.Response(status, json=json_body if json_body is not None else {})

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            client_mod,
            "_build_client",
            lambda settings: httpx.Client(base_url=settings.api_url, transport=transport),
        )
        return captured

    return _apply


def test_me_get_calls_endpoint_with_bearer(patch_client):
    captured = patch_client(200, {"identifier": "acme", "legalName": "Acme Co"})

    result = runner.invoke(app, ["--token", "tok", "me", "get"])

    assert result.exit_code == 0
    assert "acme" in result.stdout
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/members/me"
    assert captured[0].headers["authorization"] == "Bearer tok"


def test_me_update_builds_merge_patch_body(patch_client):
    captured = patch_client(200, {"identifier": "acme"})

    result = runner.invoke(
        app,
        ["--token", "tok", "me", "update", "--email", "a@b.c", "--street-address", "1 St"],
    )

    assert result.exit_code == 0
    assert captured[0].method == "PATCH"
    sent = json.loads(captured[0].content)
    # Only the passed fields, with address nested — a partial (merge) update.
    assert sent == {"email": "a@b.c", "address": {"streetAddress": "1 St"}}


def test_me_update_with_no_fields_is_usage_error(patch_client):
    patch_client()
    result = runner.invoke(app, ["--token", "tok", "me", "update"])
    assert result.exit_code == 2


def test_missing_token_is_usage_error(monkeypatch, patch_client):
    monkeypatch.delenv("DIRECTORY_TOKEN", raising=False)
    patch_client()
    result = runner.invoke(app, ["me", "get"])
    assert result.exit_code == 2


def test_api_error_exits_nonzero(patch_client):
    patch_client(403, {"detail": "forbidden"})
    result = runner.invoke(app, ["--token", "tok", "me", "get"])
    assert result.exit_code == 1


def test_me_get_falls_back_to_cached_token(monkeypatch, patch_client):
    """With no --token/env, the request uses the keyring-cached token."""
    monkeypatch.delenv("DIRECTORY_TOKEN", raising=False)
    monkeypatch.setattr(auth_mod, "get_id_token", lambda config: "cached-tok")
    captured = patch_client(200, {"identifier": "acme"})

    result = runner.invoke(app, ["me", "get"])

    assert result.exit_code == 0
    assert captured[0].headers["authorization"] == "Bearer cached-tok"


def test_login_without_config_is_usage_error(monkeypatch):
    monkeypatch.delenv("DIRECTORY_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("DIRECTORY_COGNITO_CLIENT_ID", raising=False)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 2


def test_token_command_without_cache_is_usage_error():
    result = runner.invoke(app, ["token"])  # autouse fixture: no cached token
    assert result.exit_code == 2


def test_token_command_prints_cached_token(monkeypatch):
    monkeypatch.setattr(auth_mod, "get_id_token", lambda config: "abc123")
    result = runner.invoke(app, ["token"])
    assert result.exit_code == 0
    assert "abc123" in result.stdout


# --- apps ------------------------------------------------------------------


def test_apps_list_calls_endpoint(patch_client):
    captured = patch_client(200, {"applications": []})
    result = runner.invoke(app, ["--token", "tok", "apps", "list"])
    assert result.exit_code == 0
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/members/applications"
    assert captured[0].headers["authorization"] == "Bearer tok"


def test_apps_list_scheme_filter_adds_query(patch_client):
    captured = patch_client(200, {"applications": []})
    result = runner.invoke(app, ["--token", "tok", "apps", "list", "--scheme", "energy"])
    assert result.exit_code == 0
    assert captured[0].url.params.get("scheme") == "energy"


def test_apps_get_calls_endpoint(patch_client):
    captured = patch_client(200, {"id": "http://x/a/abc123", "title": "App"})
    result = runner.invoke(app, ["--token", "tok", "apps", "get", "abc123"])
    assert result.exit_code == 0
    assert captured[0].url.path == "/members/applications/abc123"


def test_apps_create_builds_body(patch_client):
    captured = patch_client(201, {"id": "http://x/a/new"})
    result = runner.invoke(
        app,
        [
            "--token", "tok", "apps", "create",
            "--scheme", "s1", "--title", "App",
            "--role", "https://r/1", "--role", "https://r/2",
            "--home-page-url", "https://home",
        ],
    )
    assert result.exit_code == 0
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/members/applications"
    sent = json.loads(captured[0].content)
    assert sent == {
        "scheme": "s1",
        "title": "App",
        "role": ["https://r/1", "https://r/2"],
        "homePageURL": "https://home",
    }


def test_apps_create_includes_data_service(patch_client):
    captured = patch_client(201, {"id": "http://x/a/new"})
    result = runner.invoke(
        app,
        [
            "--token", "tok", "apps", "create", "--scheme", "s1", "--title", "A",
            "--data-service-title", "Svc",
            "--data-service-conforms-to", "https://standard",
            "--data-service-endpoint-url", "https://api",
        ],
    )
    assert result.exit_code == 0
    sent = json.loads(captured[0].content)
    assert sent["dataService"] == {
        "title": "Svc",
        "conformsTo": "https://standard",
        "endpointURL": "https://api",
    }


def test_apps_create_partial_data_service_is_usage_error(patch_client):
    captured = patch_client(201, {})
    result = runner.invoke(
        app,
        [
            "--token", "tok", "apps", "create", "--scheme", "s1", "--title", "A",
            "--data-service-title", "Svc",  # missing conforms-to + endpoint-url
        ],
    )
    assert result.exit_code == 2
    assert captured == []  # rejected before any request


def test_apps_update_builds_merge_patch(patch_client):
    captured = patch_client(200, {"id": "http://x/a/the-id"})
    result = runner.invoke(
        app, ["--token", "tok", "apps", "update", "the-id", "--title", "New"]
    )
    assert result.exit_code == 0
    assert captured[0].method == "PATCH"
    assert captured[0].url.path == "/members/applications/the-id"
    assert json.loads(captured[0].content) == {"title": "New"}


def test_apps_update_with_no_fields_is_usage_error(patch_client):
    captured = patch_client()
    result = runner.invoke(app, ["--token", "tok", "apps", "update", "the-id"])
    assert result.exit_code == 2
    assert captured == []


def test_apps_delete_without_yes_is_usage_error(patch_client):
    captured = patch_client()
    result = runner.invoke(app, ["--token", "tok", "apps", "delete", "the-id"])
    assert result.exit_code == 2
    assert captured == []  # never calls the API


def test_apps_delete_with_yes_calls_endpoint(patch_client):
    captured = patch_client(204)
    result = runner.invoke(app, ["--token", "tok", "apps", "delete", "the-id", "--yes"])
    assert result.exit_code == 0
    assert captured[0].method == "DELETE"
    assert captured[0].url.path == "/members/applications/the-id"


def test_apps_delete_json_emits_confirmation(patch_client):
    patch_client(204)
    result = runner.invoke(
        app, ["--token", "tok", "--json", "apps", "delete", "the-id", "--yes"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"deleted": "the-id"}


# --- ca --------------------------------------------------------------------

_ZIP = b"PK\x03\x04zip-bytes"
_DISPOSITION = 'attachment; filename="directory-client-certificates.zip"'


def test_ca_download_writes_zip_to_output(patch_client, tmp_path):
    captured = patch_client(
        200, content=_ZIP, headers={"content-disposition": _DISPOSITION}
    )
    dest = tmp_path / "bundle.zip"
    result = runner.invoke(
        app, ["--token", "tok", "ca", "download", "client", "-o", str(dest)]
    )
    assert result.exit_code == 0
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/members/ca/client/download"
    assert dest.read_bytes() == _ZIP


def test_ca_download_defaults_to_server_filename(patch_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    patch_client(
        200,
        content=_ZIP,
        headers={
            "content-disposition": 'attachment; filename="directory-signing-certificates.zip"'
        },
    )
    result = runner.invoke(app, ["--token", "tok", "ca", "download", "signing"])
    assert result.exit_code == 0
    assert (tmp_path / "directory-signing-certificates.zip").read_bytes() == _ZIP


def test_ca_download_json_reports_destination(patch_client, tmp_path):
    patch_client(200, content=_ZIP, headers={"content-disposition": _DISPOSITION})
    dest = tmp_path / "b.zip"
    result = runner.invoke(
        app, ["--token", "tok", "--json", "ca", "download", "client", "-o", str(dest)]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"downloaded": str(dest), "bytes": len(_ZIP)}


def test_ca_download_api_error_exits_nonzero(patch_client, tmp_path):
    patch_client(404, content=b"nope")
    result = runner.invoke(
        app,
        ["--token", "tok", "ca", "download", "client", "-o", str(tmp_path / "x.zip")],
    )
    assert result.exit_code == 1


def test_ca_download_requires_authentication(patch_client, tmp_path):
    patch_client()
    result = runner.invoke(
        app, ["ca", "download", "client", "-o", str(tmp_path / "x.zip")]
    )
    assert result.exit_code == 2


# --- cert ------------------------------------------------------------------

_SIGN_RESULT = {
    "id": "cert-123",
    "type": "client",
    "downloadName": "my-app-client-cert.pem",
    "certificate": "-----BEGIN CERTIFICATE-----\nsigned\n-----END CERTIFICATE-----\n",
}


def test_cert_sign_generates_key_and_saves_outputs(patch_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = patch_client(201, _SIGN_RESULT)

    result = runner.invoke(app, ["--token", "tok", "cert", "sign", "my-app", "client"])

    assert result.exit_code == 0
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/members/applications/my-app/certificates/client/sign"
    sent = json.loads(captured[0].content)
    assert "BEGIN CERTIFICATE REQUEST" in sent["csr"]  # a CSR was generated
    # The generated key and the signed cert are written to disk.
    assert (tmp_path / "my-app-client-key.pem").exists()
    assert (
        (tmp_path / "my-app-client-cert.pem").read_text()
        == _SIGN_RESULT["certificate"]
    )


def test_cert_sign_with_provided_csr_writes_no_key(patch_client, tmp_path):
    csr_path = tmp_path / "in.csr"
    csr_path.write_text("-----BEGIN CERTIFICATE REQUEST-----\nmine\n-----END CERTIFICATE REQUEST-----\n")
    captured = patch_client(201, _SIGN_RESULT)

    result = runner.invoke(
        app,
        [
            "--token", "tok", "cert", "sign", "my-app", "client",
            "--csr", str(csr_path),
            "--cert-out", str(tmp_path / "out.pem"),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(captured[0].content)["csr"] == csr_path.read_text()
    assert not (tmp_path / "my-app-client-key.pem").exists()  # user supplied the CSR
    assert (tmp_path / "out.pem").read_text() == _SIGN_RESULT["certificate"]


def test_cert_sign_sends_name_when_given(patch_client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = patch_client(201, _SIGN_RESULT)
    result = runner.invoke(
        app, ["--token", "tok", "cert", "sign", "my-app", "signing", "--name", "prod key"]
    )
    assert result.exit_code == 0
    assert json.loads(captured[0].content)["name"] == "prod key"


def test_cert_download_writes_pem(patch_client, tmp_path):
    captured = patch_client(
        200,
        content=b"CERT-PEM",
        headers={"content-disposition": 'attachment; filename="my-app-client-cert.pem"'},
    )
    dest = tmp_path / "c.pem"
    result = runner.invoke(
        app, ["--token", "tok", "cert", "download", "cert-123", "-o", str(dest)]
    )
    assert result.exit_code == 0
    assert captured[0].url.path == "/members/certificates/cert-123/download"
    assert dest.read_bytes() == b"CERT-PEM"


def test_cert_revoke_without_yes_is_usage_error(patch_client):
    captured = patch_client()
    result = runner.invoke(app, ["--token", "tok", "cert", "revoke", "cert-123"])
    assert result.exit_code == 2
    assert captured == []  # never calls the API


def test_cert_revoke_with_yes_calls_endpoint(patch_client):
    captured = patch_client(200, {"id": "cert-123", "revoked": "2026-07-15T00:00:00Z"})
    result = runner.invoke(
        app, ["--token", "tok", "cert", "revoke", "cert-123", "--yes"]
    )
    assert result.exit_code == 0
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/members/certificates/cert-123/revoke"
