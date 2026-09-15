"""Interactive login for the Directory CLI.

Authorization-code + PKCE against the Cognito Hosted UI, with the access/refresh tokens
cached in the OS keyring. The CLI is a public client (no secret), so PKCE is what proves
the token request came from the same client that started the login.

The Cognito hosted UI domain, client id and scopes are discovered from the API being
logged in to (GET /.well-known/directory-cli), so the API URL is the only thing a user sets. Any of
them can be overridden from the environment, e.g. against an API that does not publish them:

  DIRECTORY_COGNITO_DOMAIN     e.g. https://<prefix>.auth.eu-west-2.amazoncognito.com
  DIRECTORY_COGNITO_CLIENT_ID  the public (no-secret) CLI app client id
  DIRECTORY_OAUTH_SCOPES       space-separated, default "openid email"
  DIRECTORY_REDIRECT_PORT      default 8400 (must match the client's registered callback)

Tokens are cached per API URL, together with the domain and client id that issued them, so
refreshing a token never needs the API.
"""

from __future__ import annotations

import http.server
import json
import os
import time
import webbrowser
from dataclasses import dataclass

import httpx2
import keyring
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import OAuth2Client

_KEYRING_SERVICE = "directory-cli"
# Refresh a bit before the real expiry so a token handed out is still valid in flight.
_EXPIRY_SKEW_SECONDS = 30
LOGIN_CONFIG_PATH = "/.well-known/directory-cli"
_DEFAULT_SCOPES = "openid email"
_DEFAULT_REDIRECT_PORT = 8400


class LoginConfigError(Exception):
    """The login configuration could not be fetched from the API."""


class LoginNotConfigured(LoginConfigError):
    """The API does not publish login configuration, and none was set locally."""


@dataclass
class AuthConfig:
    domain: str
    client_id: str
    scopes: str = _DEFAULT_SCOPES
    redirect_port: int = _DEFAULT_REDIRECT_PORT

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.redirect_port}/callback"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.domain}/oauth2/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.domain}/oauth2/token"

    @property
    def logout_endpoint(self) -> str:
        return f"{self.domain}/logout"


def _build_http_client(api_url: str) -> httpx2.Client:
    # Tests patch this to inject an httpx2.MockTransport (no real network calls).
    return httpx2.Client(base_url=api_url, timeout=10.0)


def fetch_login_config(api_url: str) -> dict:
    """Fetch the Cognito domain, client id and scopes the API publishes for CLI login.

    Raises LoginNotConfigured on a 404 (an environment without CLI login, or an API that
    predates the endpoint), LoginConfigError on any other unusable response, and lets
    httpx2 transport errors propagate.
    """
    with _build_http_client(api_url) as http:
        response = http.get(LOGIN_CONFIG_PATH)
    if response.status_code == 404:
        raise LoginNotConfigured(
            f"{api_url} does not publish CLI login configuration. Check the API URL, or set "
            "DIRECTORY_COGNITO_DOMAIN and DIRECTORY_COGNITO_CLIENT_ID."
        )
    if response.status_code >= 400:
        raise LoginConfigError(
            f"Fetching login configuration from {api_url} returned {response.status_code}"
        )
    try:
        body = response.json()
        return {
            "domain": body["cognitoDomain"],
            "client_id": body["clientId"],
            "scopes": " ".join(body["scopes"]),
        }
    # A path the API doesn't serve can land on the web frontend and come back as HTML.
    except (ValueError, KeyError, TypeError) as exc:
        raise LoginConfigError(
            f"{api_url}{LOGIN_CONFIG_PATH} did not return login configuration"
        ) from exc


def resolve_auth_config(api_url: str) -> AuthConfig:
    """Login settings for this API: environment overrides first, then the API's own.

    The API is only asked when the environment doesn't already supply both the domain and
    the client id.
    """
    domain = os.environ.get("DIRECTORY_COGNITO_DOMAIN")
    client_id = os.environ.get("DIRECTORY_COGNITO_CLIENT_ID")
    scopes = os.environ.get("DIRECTORY_OAUTH_SCOPES")
    if not (domain and client_id):
        discovered = fetch_login_config(api_url)
        domain = domain or discovered["domain"]
        client_id = client_id or discovered["client_id"]
        scopes = scopes or discovered["scopes"]
    return AuthConfig(
        domain=domain.rstrip("/"),
        client_id=client_id,
        scopes=scopes or _DEFAULT_SCOPES,
        redirect_port=int(
            os.environ.get("DIRECTORY_REDIRECT_PORT", _DEFAULT_REDIRECT_PORT)
        ),
    )


# --- token cache (OS keyring) --------------------------------------------------------


def _account(api_url: str) -> str:
    # Key by API URL so different environments don't share a cache entry.
    return api_url.rstrip("/")


def _store_token(api_url: str, config: AuthConfig, token: dict) -> None:
    account = _account(api_url)
    # The domain and client id travel with the token: a refresh token is only valid for
    # the client that issued it, so refreshing must not depend on what the API says now.
    payload = json.dumps(
        {"domain": config.domain, "client_id": config.client_id, "token": token}
    )
    try:
        keyring.set_password(_KEYRING_SERVICE, account, payload)
    except keyring.errors.PasswordSetError:
        # On macOS, keyring updates an existing item in place, and the keychain refuses
        # to modify one whose access control list is owned by a different binary
        # (errSecInvalidOwnerEdit / -25244) - e.g. after the CLI is reinstalled under a
        # different Python. Delete the stale item and store fresh, which creates a new
        # item owned by the current process.
        try:
            keyring.delete_password(_KEYRING_SERVICE, account)
        except keyring.errors.PasswordDeleteError:
            pass
        keyring.set_password(_KEYRING_SERVICE, account, payload)


def _load_entry(api_url: str) -> tuple[AuthConfig, dict] | None:
    """The cached (config, token) for this API, or None if there is no usable entry."""
    raw = keyring.get_password(_KEYRING_SERVICE, _account(api_url))
    if not raw:
        return None
    entry = json.loads(raw)
    if (
        not isinstance(entry, dict)
        or not {"domain", "client_id", "token"} <= entry.keys()
    ):
        return None
    config = AuthConfig(domain=entry["domain"], client_id=entry["client_id"])
    return config, entry["token"]


def _clear_token(api_url: str) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, _account(api_url))
    except keyring.errors.PasswordDeleteError:
        pass


# --- flow ----------------------------------------------------------------------------


def _capture_redirect(config: AuthConfig, authorization_url: str) -> str:
    """Open the browser and block until Cognito redirects back to the loopback URL."""
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API name)
            if self.path.startswith("/callback"):
                captured["path"] = self.path
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body>Login complete. You can close this tab.</body></html>"
                )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args) -> None:  # silence default stderr logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", config.redirect_port), Handler)
    webbrowser.open(authorization_url)
    try:
        while "path" not in captured:
            server.handle_request()  # one request at a time; ignores favicon etc.
    finally:
        server.server_close()
    return f"http://localhost:{config.redirect_port}{captured['path']}"


def login(api_url: str, config: AuthConfig) -> dict:
    """Run the interactive login and cache the resulting tokens. Returns the token dict."""
    code_verifier = generate_token(48)
    with OAuth2Client(
        client_id=config.client_id,
        scope=config.scopes,
        redirect_uri=config.redirect_uri,
        code_challenge_method="S256",
        token_endpoint_auth_method="none",
    ) as client:
        authorization_url, _state = client.create_authorization_url(
            config.authorize_endpoint, code_verifier=code_verifier
        )
        redirect_response = _capture_redirect(config, authorization_url)
        token = client.fetch_token(
            config.token_endpoint,
            authorization_response=redirect_response,
            code_verifier=code_verifier,
        )
    token = dict(token)
    _store_token(api_url, config, token)
    return token


def logout(api_url: str) -> None:
    """Clear the cached tokens for this API."""
    _clear_token(api_url)


def get_id_token(api_url: str) -> str | None:
    """Return a usable id token from the cache, refreshing it if expired.

    The member API authenticates with the id token (it carries the user's email, which
    the API matches to their organisation). Returns None when there is nothing cached, or
    it has expired and cannot be refreshed.
    """
    entry = _load_entry(api_url)
    if not entry:
        return None
    config, token = entry

    expires_at = token.get("expires_at")
    if not expires_at or time.time() < expires_at - _EXPIRY_SKEW_SECONDS:
        return token.get("id_token")

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return None
    with OAuth2Client(
        client_id=config.client_id, token_endpoint_auth_method="none"
    ) as client:
        new_token = dict(
            client.refresh_token(config.token_endpoint, refresh_token=refresh_token)
        )
    # Cognito does not return a new refresh token on refresh; keep the existing one.
    new_token.setdefault("refresh_token", refresh_token)
    _store_token(api_url, config, new_token)
    return new_token.get("id_token")
