"""Typer entry point for the Directory CLI.

A non-interactive harness for the member API: token from ``--token`` / ``DIRECTORY_TOKEN``,
machine-readable output (``--json``), and meaningful exit codes so an agent or CI can drive
it. Exit codes: 0 success, 1 API/transport error, 2 usage error (e.g. no token).
"""

from __future__ import annotations

import json as jsonlib
from typing import Optional

import httpx
import typer

from directory_cli import client
from directory_cli.client import Settings

app = typer.Typer(
    help="Command-line client for the IB1 Directory member API.",
    no_args_is_help=True,
    add_completion=False,
)
me_app = typer.Typer(help="Operate on your own organisation.", no_args_is_help=True)
app.add_typer(me_app, name="me")


@app.callback()
def main(
    ctx: typer.Context,
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api-url",
        envvar="DIRECTORY_API_URL",
        help="Base URL of the Directory API.",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        envvar="DIRECTORY_TOKEN",
        help="Bearer access token (or set DIRECTORY_TOKEN).",
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Emit compact JSON (default: pretty-printed)."
    ),
) -> None:
    ctx.obj = Settings(api_url=api_url, token=token, output_json=output_json)


def _emit(settings: Settings, data) -> None:
    if settings.output_json:
        typer.echo(jsonlib.dumps(data))
    else:
        typer.echo(jsonlib.dumps(data, indent=2))


def _call(settings: Settings, method: str, path: str, body: dict | None = None):
    """Run a request, mapping failures to messages on stderr + exit codes."""
    try:
        return client.request(settings, method, path, json=body)
    except client.MissingToken:
        typer.secho(
            "No token. Pass --token or set DIRECTORY_TOKEN.", fg="red", err=True
        )
        raise typer.Exit(2)
    except client.APIError as exc:
        typer.secho(f"API error: {exc.status}", fg="red", err=True)
        if exc.body:
            typer.echo(exc.body, err=True)
        raise typer.Exit(1)
    except httpx.HTTPError as exc:
        typer.secho(f"Request failed: {exc}", fg="red", err=True)
        raise typer.Exit(1)


@me_app.command("get")
def me_get(ctx: typer.Context) -> None:
    """Fetch your organisation (GET /members/me)."""
    settings: Settings = ctx.obj
    _emit(settings, _call(settings, "GET", "/members/me"))


@me_app.command("update")
def me_update(
    ctx: typer.Context,
    email: Optional[str] = typer.Option(None, help="Contact email."),
    street_address: Optional[str] = typer.Option(None, "--street-address"),
    locality: Optional[str] = typer.Option(None),
    region: Optional[str] = typer.Option(None),
    state: Optional[str] = typer.Option(None),
    postal_code: Optional[str] = typer.Option(None, "--postal-code"),
    country: Optional[str] = typer.Option(None, help="2-letter country code."),
    privacy_policy: Optional[str] = typer.Option(None, "--privacy-policy"),
    data_protection_url: Optional[str] = typer.Option(None, "--data-protection-url"),
) -> None:
    """Update your organisation's editable fields (PATCH /members/me).

    Only the flags you pass are sent, so this is a partial (merge-patch) update.
    """
    settings: Settings = ctx.obj

    address = {
        "streetAddress": street_address,
        "locality": locality,
        "region": region,
        "state": state,
        "postalCode": postal_code,
        "country": country,
    }
    address = {k: v for k, v in address.items() if v is not None}

    body: dict = {}
    if email is not None:
        body["email"] = email
    if privacy_policy is not None:
        body["privacyPolicy"] = privacy_policy
    if data_protection_url is not None:
        body["dataProtectionRegistrationUrl"] = data_protection_url
    if address:
        body["address"] = address

    if not body:
        typer.secho("Nothing to update: pass at least one field.", fg="red", err=True)
        raise typer.Exit(2)

    _emit(settings, _call(settings, "PATCH", "/members/me", body=body))
