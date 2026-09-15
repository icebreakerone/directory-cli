"""Tests for the auth module: token cache and refresh logic.

The OS keyring is replaced with an in-memory store and the OAuth2 client is faked, so
the browser flow and real network are never exercised (those need the provisioned Cognito
client and are covered by manual verification).
"""

import json
import time

import httpx
import pytest

from directory_cli import auth


@pytest.fixture
def fake_keyring(monkeypatch):
    """Replace keyring storage with an in-memory dict; return it for assertions."""
    store: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(auth.keyring, "get_password", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(
        auth.keyring, "set_password", lambda s, a, v: store.__setitem__((s, a), v)
    )

    def _delete(s, a):
        if (s, a) in store:
            del store[(s, a)]
        else:
            raise auth.keyring.errors.PasswordDeleteError()

    monkeypatch.setattr(auth.keyring, "delete_password", _delete)
    return store


API_URL = "https://directory.example.org"


@pytest.fixture(autouse=True)
def no_login_env(monkeypatch):
    """Start from no local login overrides, whatever the developer's shell or .env sets."""
    for name in (
        "DIRECTORY_COGNITO_DOMAIN",
        "DIRECTORY_COGNITO_CLIENT_ID",
        "DIRECTORY_OAUTH_SCOPES",
        "DIRECTORY_REDIRECT_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def login_config_api(monkeypatch):
    """Serve GET /.well-known/directory-cli from a mock transport; return the captured requests."""

    def _apply(status: int = 200, json_body=None, content=None):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if content is not None:
                return httpx.Response(status, content=content)
            return httpx.Response(status, json=json_body)

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            auth,
            "_build_http_client",
            lambda api_url: httpx.Client(base_url=api_url, transport=transport),
        )
        return captured

    return _apply


PUBLISHED = {
    "cognitoDomain": "https://d.auth.eu-west-2.amazoncognito.com",
    "clientId": "cli-client",
    "scopes": ["openid", "email"],
}


def _config(**kwargs):
    base = dict(
        domain="https://d.auth.eu-west-2.amazoncognito.com",
        client_id="cli-client",
    )
    base.update(kwargs)
    return auth.AuthConfig(**base)


# --- login configuration ---------------------------------------------------------------


def test_resolve_auth_config_discovers_from_api(login_config_api):
    captured = login_config_api(200, PUBLISHED)

    config = auth.resolve_auth_config(API_URL)

    assert captured[0].url == f"{API_URL}/.well-known/directory-cli"
    assert config.domain == "https://d.auth.eu-west-2.amazoncognito.com"
    assert config.client_id == "cli-client"
    assert config.scopes == "openid email"
    assert config.redirect_uri == "http://localhost:8400/callback"


def test_resolve_auth_config_env_skips_the_api(monkeypatch, login_config_api):
    captured = login_config_api(500)
    monkeypatch.setenv("DIRECTORY_COGNITO_DOMAIN", "https://x/")
    monkeypatch.setenv("DIRECTORY_COGNITO_CLIENT_ID", "abc")

    config = auth.resolve_auth_config(API_URL)

    assert captured == []
    assert config.domain == "https://x"
    assert config.client_id == "abc"
    assert config.scopes == "openid email"


def test_resolve_auth_config_env_overrides_single_field(monkeypatch, login_config_api):
    login_config_api(200, PUBLISHED)
    monkeypatch.setenv("DIRECTORY_COGNITO_CLIENT_ID", "override")

    config = auth.resolve_auth_config(API_URL)

    assert config.client_id == "override"
    assert config.domain == PUBLISHED["cognitoDomain"]


def test_resolve_auth_config_not_published(login_config_api):
    login_config_api(
        404, {"detail": "CLI login is not configured for this environment"}
    )
    with pytest.raises(auth.LoginNotConfigured):
        auth.resolve_auth_config(API_URL)


def test_resolve_auth_config_rejects_non_json(login_config_api):
    # An unrouted path can fall through to the web frontend and return a page.
    login_config_api(200, content=b"<html>frontend</html>")
    with pytest.raises(auth.LoginConfigError):
        auth.resolve_auth_config(API_URL)


def test_resolve_auth_config_rejects_server_error(login_config_api):
    login_config_api(502, {})
    with pytest.raises(auth.LoginConfigError):
        auth.resolve_auth_config(API_URL)


# --- token cache -----------------------------------------------------------------------


def test_token_round_trips_through_cache(fake_keyring):
    config = _config()
    auth._store_token(
        API_URL, config, {"access_token": "a", "expires_at": time.time() + 999}
    )
    cached_config, token = auth._load_entry(API_URL)
    assert token["access_token"] == "a"
    assert cached_config.client_id == "cli-client"


def test_cache_is_keyed_by_api_url(fake_keyring):
    auth._store_token(
        API_URL, _config(), {"id_token": "t", "expires_at": time.time() + 999}
    )
    assert auth.get_id_token(f"{API_URL}/") == "t"
    assert auth.get_id_token("https://other.example.org") is None


def test_cache_ignores_entries_without_login_config(fake_keyring):
    fake_keyring[(auth._KEYRING_SERVICE, API_URL)] = json.dumps({"id_token": "flat"})
    assert auth.get_id_token(API_URL) is None


def test_get_id_token_returns_valid_cached(fake_keyring):
    auth._store_token(
        API_URL, _config(), {"id_token": "valid", "expires_at": time.time() + 999}
    )
    assert auth.get_id_token(API_URL) == "valid"


def test_get_id_token_none_when_empty(fake_keyring):
    assert auth.get_id_token(API_URL) is None


def test_get_id_token_refreshes_when_expired(fake_keyring, monkeypatch):
    config = _config(client_id="issuing-client")
    auth._store_token(
        API_URL,
        config,
        {"id_token": "old", "refresh_token": "r1", "expires_at": time.time() - 10},
    )

    class FakeClient:
        def __init__(self, **kwargs):
            # Refresh uses the client cached with the token, with no call to the API.
            assert kwargs["client_id"] == "issuing-client"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def refresh_token(self, endpoint, refresh_token):
            assert endpoint == "https://d.auth.eu-west-2.amazoncognito.com/oauth2/token"
            assert refresh_token == "r1"
            return {"id_token": "fresh", "expires_at": time.time() + 999}

    monkeypatch.setattr(auth, "OAuth2Client", FakeClient)

    assert auth.get_id_token(API_URL) == "fresh"
    # Cognito doesn't reissue the refresh token; the old one is preserved.
    assert auth._load_entry(API_URL)[1]["refresh_token"] == "r1"


def test_logout_clears_cache(fake_keyring):
    auth._store_token(API_URL, _config(), {"access_token": "a"})
    auth.logout(API_URL)
    assert auth._load_entry(API_URL) is None


def test_store_token_retries_after_owner_conflict(monkeypatch):
    """A macOS keychain owner-edit failure (-25244) triggers delete then re-store."""
    store: dict[tuple[str, str], str] = {}
    set_calls = {"n": 0}

    def set_password(s, a, v):
        set_calls["n"] += 1
        # First attempt hits the pre-existing (foreign-owned) item and is refused.
        if set_calls["n"] == 1 and (s, a) in store:
            raise auth.keyring.errors.PasswordSetError(
                "Can't store password on keychain"
            )
        store[(s, a)] = v

    monkeypatch.setattr(auth.keyring, "get_password", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(auth.keyring, "set_password", set_password)
    monkeypatch.setattr(
        auth.keyring, "delete_password", lambda s, a: store.pop((s, a), None)
    )

    store[(auth._KEYRING_SERVICE, auth._account(API_URL))] = "stale"

    auth._store_token(API_URL, _config(), {"id_token": "new"})

    assert auth._load_entry(API_URL)[1]["id_token"] == "new"
    assert set_calls["n"] == 2  # first failed, second succeeded after delete
