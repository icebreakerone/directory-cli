"""Interactive login for the Directory CLI.

Authorization-code + PKCE against the Cognito Hosted UI, with the access/refresh tokens
cached in the OS keyring. The CLI is a public client (no secret), so PKCE is what proves
the token request came from the same client that started the login.

Configured from the environment (the public Cognito client is environment-specific):

  DIRECTORY_COGNITO_DOMAIN     e.g. https://<prefix>.auth.eu-west-2.amazoncognito.com
  DIRECTORY_COGNITO_CLIENT_ID  the public (no-secret) CLI app client id
  DIRECTORY_OAUTH_SCOPES       default "openid email"
  DIRECTORY_REDIRECT_PORT      default 8400 (must match the client's registered callback)
"""

from __future__ import annotations

import http.server
import json
import os
import time
import webbrowser
from dataclasses import dataclass

import keyring
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import OAuth2Client

_KEYRING_SERVICE = "directory-cli"
# Refresh a bit before the real expiry so a token handed out is still valid in flight.
_EXPIRY_SKEW_SECONDS = 30


@dataclass
class AuthConfig:
    domain: str | None
    client_id: str | None
    scopes: str
    redirect_port: int

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

    def missing_for_login(self) -> list[str]:
        missing = []
        if not self.domain:
            missing.append("DIRECTORY_COGNITO_DOMAIN")
        if not self.client_id:
            missing.append("DIRECTORY_COGNITO_CLIENT_ID")
        return missing


def load_auth_config() -> AuthConfig:
    return AuthConfig(
        domain=os.environ.get("DIRECTORY_COGNITO_DOMAIN"),
        client_id=os.environ.get("DIRECTORY_COGNITO_CLIENT_ID"),
        scopes=os.environ.get("DIRECTORY_OAUTH_SCOPES", "openid email"),
        redirect_port=int(os.environ.get("DIRECTORY_REDIRECT_PORT", "8400")),
    )


# --- token cache (OS keyring) --------------------------------------------------------


def _account(config: AuthConfig) -> str:
    # Key by client id so different environments don't share a cache entry.
    return config.client_id or "default"


def _store_token(config: AuthConfig, token: dict) -> None:
    keyring.set_password(_KEYRING_SERVICE, _account(config), json.dumps(token))


def _load_token(config: AuthConfig) -> dict | None:
    raw = keyring.get_password(_KEYRING_SERVICE, _account(config))
    return json.loads(raw) if raw else None


def _clear_token(config: AuthConfig) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, _account(config))
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


def login(config: AuthConfig) -> dict:
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
    _store_token(config, token)
    return token


def logout(config: AuthConfig) -> None:
    """Clear the cached tokens."""
    _clear_token(config)


def get_id_token(config: AuthConfig) -> str | None:
    """Return a usable id token from the cache, refreshing it if expired.

    The member API authenticates with the id token (it carries the user's email, which
    the API matches to their organisation). Returns None when there is nothing cached, or
    it has expired and cannot be refreshed.
    """
    token = _load_token(config)
    if not token:
        return None

    expires_at = token.get("expires_at")
    if not expires_at or time.time() < expires_at - _EXPIRY_SKEW_SECONDS:
        return token.get("id_token")

    refresh_token = token.get("refresh_token")
    if not refresh_token or not config.domain or not config.client_id:
        return None
    with OAuth2Client(
        client_id=config.client_id, token_endpoint_auth_method="none"
    ) as client:
        new_token = dict(
            client.refresh_token(config.token_endpoint, refresh_token=refresh_token)
        )
    # Cognito does not return a new refresh token on refresh; keep the existing one.
    new_token.setdefault("refresh_token", refresh_token)
    _store_token(config, new_token)
    return new_token.get("id_token")
