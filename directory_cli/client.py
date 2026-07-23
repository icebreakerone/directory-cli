"""Thin HTTP layer for the Directory API.

Token-paste mode: the bearer token is supplied by the caller (``--token`` or the
``DIRECTORY_TOKEN`` env var). Interactive login (PKCE) and a keyring cache come in a
later slice; this module deliberately knows nothing about how the token was obtained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


@dataclass
class Settings:
    """Resolved global options, carried on the Typer context."""

    api_url: str
    token: str | None
    output_json: bool
    organization: str | None = None


class MissingToken(Exception):
    """No bearer token was provided."""


class APIError(Exception):
    """The API returned a 4xx/5xx response."""

    def __init__(self, status: int, body: str):
        super().__init__(f"API returned {status}")
        self.status = status
        self.body = body


def _build_client(settings: Settings) -> httpx.Client:
    # Tests patch this to inject an httpx.MockTransport (no real network calls).
    return httpx.Client(base_url=settings.api_url, timeout=10.0)


def _auth_headers(settings: Settings) -> dict[str, str]:
    """Bearer header, plus the organisation selector when one is set.

    A caller who owns a single organisation omits the selector. A caller who owns several
    sets `--organization` / DIRECTORY_ORGANIZATION so the API knows which one to act as.
    """
    if not settings.token:
        raise MissingToken()
    headers = {"Authorization": f"Bearer {settings.token}"}
    if settings.organization:
        headers["X-Organization"] = settings.organization
    return headers


def request(settings: Settings, method: str, path: str, json: dict | None = None):
    """Call the API with the bearer token, returning the parsed JSON body."""
    headers = _auth_headers(settings)
    with _build_client(settings) as client:
        response = client.request(method, path, headers=headers, json=json)
    if response.status_code >= 400:
        raise APIError(response.status_code, response.text)
    # DELETE returns 204 with no body; anything empty has nothing to parse.
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _filename_from_disposition(value: str | None) -> str | None:
    """Pull the filename out of a Content-Disposition header, if present."""
    if not value:
        return None
    match = re.search(r'filename="?([^";]+)"?', value)
    return match.group(1) if match else None


def download(settings: Settings, path: str) -> tuple[bytes, str | None]:
    """GET a binary response, returning its raw bytes and the server's suggested filename.

    Used for endpoints that return a file (e.g. the CA bundle ZIP) rather than JSON.
    """
    headers = _auth_headers(settings)
    with _build_client(settings) as client:
        response = client.request("GET", path, headers=headers)
    if response.status_code >= 400:
        raise APIError(response.status_code, response.text)
    return response.content, _filename_from_disposition(
        response.headers.get("content-disposition")
    )
