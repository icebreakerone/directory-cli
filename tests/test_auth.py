"""Tests for the auth module: token cache and refresh logic.

The OS keyring is replaced with an in-memory store and the OAuth2 client is faked, so
the browser flow and real network are never exercised (those need the provisioned Cognito
client and are covered by manual verification).
"""

import time

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


def _config(**kwargs):
    base = dict(
        domain="https://d.auth.eu-west-2.amazoncognito.com",
        client_id="cli-client",
        scopes="openid email",
        redirect_port=8400,
    )
    base.update(kwargs)
    return auth.AuthConfig(**base)


def test_load_auth_config_reads_env(monkeypatch):
    monkeypatch.setenv("DIRECTORY_COGNITO_DOMAIN", "https://x")
    monkeypatch.setenv("DIRECTORY_COGNITO_CLIENT_ID", "abc")
    config = auth.load_auth_config()
    assert config.domain == "https://x"
    assert config.client_id == "abc"
    assert config.redirect_uri == "http://localhost:8400/callback"
    assert config.missing_for_login() == []


def test_missing_for_login_lists_absent_fields():
    config = auth.AuthConfig(domain=None, client_id=None, scopes="openid", redirect_port=8400)
    assert set(config.missing_for_login()) == {
        "DIRECTORY_COGNITO_DOMAIN",
        "DIRECTORY_COGNITO_CLIENT_ID",
    }


def test_token_round_trips_through_cache(fake_keyring):
    config = _config()
    auth._store_token(config, {"access_token": "a", "expires_at": time.time() + 999})
    assert auth._load_token(config)["access_token"] == "a"


def test_get_id_token_returns_valid_cached(fake_keyring):
    config = _config()
    auth._store_token(config, {"id_token": "valid", "expires_at": time.time() + 999})
    assert auth.get_id_token(config) == "valid"


def test_get_id_token_none_when_empty(fake_keyring):
    assert auth.get_id_token(_config()) is None


def test_get_id_token_refreshes_when_expired(fake_keyring, monkeypatch):
    config = _config()
    auth._store_token(
        config,
        {"id_token": "old", "refresh_token": "r1", "expires_at": time.time() - 10},
    )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def refresh_token(self, endpoint, refresh_token):
            assert refresh_token == "r1"
            return {"id_token": "fresh", "expires_at": time.time() + 999}

    monkeypatch.setattr(auth, "OAuth2Client", FakeClient)

    assert auth.get_id_token(config) == "fresh"
    # Cognito doesn't reissue the refresh token; the old one is preserved.
    assert auth._load_token(config)["refresh_token"] == "r1"


def test_logout_clears_cache(fake_keyring):
    config = _config()
    auth._store_token(config, {"access_token": "a"})
    auth.logout(config)
    assert auth._load_token(config) is None
