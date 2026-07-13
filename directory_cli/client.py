"""Thin HTTP layer for the Directory API.

Token-paste mode: the bearer token is supplied by the caller (``--token`` or the
``DIRECTORY_TOKEN`` env var). Interactive login (PKCE) and a keyring cache come in a
later slice; this module deliberately knows nothing about how the token was obtained.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class Settings:
    """Resolved global options, carried on the Typer context."""

    api_url: str
    token: str | None
    output_json: bool


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


def request(settings: Settings, method: str, path: str, json: dict | None = None):
    """Call the API with the bearer token, returning the parsed JSON body."""
    if not settings.token:
        raise MissingToken()
    headers = {"Authorization": f"Bearer {settings.token}"}
    with _build_client(settings) as client:
        response = client.request(method, path, headers=headers, json=json)
    if response.status_code >= 400:
        raise APIError(response.status_code, response.text)
    # DELETE returns 204 with no body; anything empty has nothing to parse.
    if response.status_code == 204 or not response.content:
        return None
    return response.json()
