"""Typer entry point for the Directory CLI.

A non-interactive harness for the member API: token from ``--token`` / ``DIRECTORY_TOKEN``,
the keyring cache (after ``directory login``), machine-readable output (``--json``), and
meaningful exit codes so an agent or CI can drive it. Exit codes: 0 success, 1 API/transport
error, 2 usage error (e.g. no token).
"""

from __future__ import annotations

import json as jsonlib
import os
from typing import List, Optional
from urllib.parse import quote

import httpx
import typer

from directory_cli import auth, client
from directory_cli.client import Settings
from directory_cli.csr import generate_key_and_csr
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(
    help="Command-line client for the IB1 Directory member API.",
    no_args_is_help=True,
    add_completion=False,
)
me_app = typer.Typer(help="Operate on your own organisation.", no_args_is_help=True)
app.add_typer(me_app, name="me")
apps_app = typer.Typer(help="Manage your applications.", no_args_is_help=True)
app.add_typer(apps_app, name="apps")
ca_app = typer.Typer(help="Certificate authority bundles.", no_args_is_help=True)
app.add_typer(ca_app, name="ca")
cert_app = typer.Typer(
    help="Sign, download and revoke certificates.", no_args_is_help=True
)
app.add_typer(cert_app, name="cert")
admin_app = typer.Typer(
    help="Administrator actions (onboarding). Requires the Cognito admin group.",
    no_args_is_help=True,
)
app.add_typer(admin_app, name="admin")


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
        help="Bearer id token (or set DIRECTORY_TOKEN).",
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


def _resolve_token(settings: Settings) -> None:
    """Fill in the token from the keyring cache if none was given explicitly.

    Precedence: --token / DIRECTORY_TOKEN (already on settings) then `directory login`.
    """
    if not settings.token:
        settings.token = auth.get_id_token(auth.load_auth_config())


def _with_api_errors(thunk):
    """Run an API call, mapping failures to stderr messages + exit codes."""
    try:
        return thunk()
    except client.MissingToken:
        typer.secho(
            "No token. Run `directory login`, pass --token, or set DIRECTORY_TOKEN.",
            fg="red",
            err=True,
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


def _call(settings: Settings, method: str, path: str, body: dict | None = None):
    """Run a request, mapping failures to messages on stderr + exit codes."""
    return _with_api_errors(lambda: client.request(settings, method, path, json=body))


@app.command("login")
def login_cmd(ctx: typer.Context) -> None:
    """Log in via the browser (authorization-code + PKCE) and cache the token."""
    config = auth.load_auth_config()
    missing = config.missing_for_login()
    if missing:
        typer.secho(
            f"Missing config: {', '.join(missing)}. See the README for the env vars.",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    try:
        auth.login(config)
    except Exception as exc:  # browser/exchange failures
        typer.secho(f"Login failed: {exc}", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho("Logged in. Token cached.", fg="green")


@app.command("logout")
def logout_cmd(ctx: typer.Context) -> None:
    """Clear the cached token."""
    auth.logout(auth.load_auth_config())
    typer.echo("Logged out.")


@app.command("token")
def token_cmd(ctx: typer.Context) -> None:
    """Print a current id token (for piping into an agent or another tool)."""
    id_token = auth.get_id_token(auth.load_auth_config())
    if not id_token:
        typer.secho("No cached token. Run `directory login`.", fg="red", err=True)
        raise typer.Exit(2)
    typer.echo(id_token)


@me_app.command("get")
def me_get(ctx: typer.Context) -> None:
    """Fetch your organisation (GET /members/me)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)
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
    _resolve_token(settings)

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


def _build_data_service(
    title: Optional[str],
    conforms_to: Optional[str],
    endpoint_url: Optional[str],
    oauth_issuer: Optional[str],
) -> Optional[dict]:
    """Build the dataService block, or None if no data-service flags were given.

    The three core fields must be supplied together, so a partial block is caught here
    with an actionable message rather than as a raw 422 from the API.
    """
    fields = {
        "title": title,
        "conformsTo": conforms_to,
        "endpointURL": endpoint_url,
        "oauthIssuer": oauth_issuer,
    }
    provided = {key: value for key, value in fields.items() if value is not None}
    if not provided:
        return None
    missing = {"title", "conformsTo", "endpointURL"} - provided.keys()
    if missing:
        typer.secho(
            "--data-service needs title, conforms-to and endpoint-url together "
            f"(missing: {', '.join(sorted(missing))}).",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    return provided


@apps_app.command("list")
def apps_list(
    ctx: typer.Context,
    scheme: Optional[str] = typer.Option(None, help="Filter by scheme short name."),
) -> None:
    """List your applications (GET /members/applications)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)
    path = "/members/applications"
    if scheme is not None:
        path += f"?scheme={quote(scheme)}"
    _emit(settings, _call(settings, "GET", path))


@apps_app.command("get")
def apps_get(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Application identifier."),
) -> None:
    """Fetch one application (GET /members/applications/{identifier})."""
    settings: Settings = ctx.obj
    _resolve_token(settings)
    _emit(settings, _call(settings, "GET", f"/members/applications/{identifier}"))


@apps_app.command("create")
def apps_create(
    ctx: typer.Context,
    scheme: str = typer.Option(
        ..., help="Scheme short name to create the application under."
    ),
    title: str = typer.Option(..., help="Application name."),
    description: Optional[str] = typer.Option(None),
    role: Optional[List[str]] = typer.Option(
        None, help="Claimed role identifier URL (repeat for several)."
    ),
    home_page_url: Optional[str] = typer.Option(None, "--home-page-url"),
    support_url: Optional[str] = typer.Option(None, "--support-url"),
    message_delivery: Optional[str] = typer.Option(None, "--message-delivery"),
    data_service_title: Optional[str] = typer.Option(None, "--data-service-title"),
    data_service_conforms_to: Optional[str] = typer.Option(
        None, "--data-service-conforms-to"
    ),
    data_service_endpoint_url: Optional[str] = typer.Option(
        None, "--data-service-endpoint-url"
    ),
    data_service_oauth_issuer: Optional[str] = typer.Option(
        None, "--data-service-oauth-issuer"
    ),
) -> None:
    """Create an application (POST /members/applications)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)

    body: dict = {"scheme": scheme, "title": title}
    if description is not None:
        body["description"] = description
    if role:
        body["role"] = role
    if home_page_url is not None:
        body["homePageURL"] = home_page_url
    if support_url is not None:
        body["supportURL"] = support_url
    if message_delivery is not None:
        body["messageDelivery"] = message_delivery
    data_service = _build_data_service(
        data_service_title,
        data_service_conforms_to,
        data_service_endpoint_url,
        data_service_oauth_issuer,
    )
    if data_service is not None:
        body["dataService"] = data_service

    _emit(settings, _call(settings, "POST", "/members/applications", body=body))


@apps_app.command("update")
def apps_update(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Application identifier."),
    title: Optional[str] = typer.Option(None),
    description: Optional[str] = typer.Option(None),
    role: Optional[List[str]] = typer.Option(
        None, help="Replace the claimed-role set (repeat for several)."
    ),
    home_page_url: Optional[str] = typer.Option(None, "--home-page-url"),
    support_url: Optional[str] = typer.Option(None, "--support-url"),
    message_delivery: Optional[str] = typer.Option(None, "--message-delivery"),
    data_service_title: Optional[str] = typer.Option(None, "--data-service-title"),
    data_service_conforms_to: Optional[str] = typer.Option(
        None, "--data-service-conforms-to"
    ),
    data_service_endpoint_url: Optional[str] = typer.Option(
        None, "--data-service-endpoint-url"
    ),
    data_service_oauth_issuer: Optional[str] = typer.Option(
        None, "--data-service-oauth-issuer"
    ),
) -> None:
    """Update an application (PATCH /members/applications/{identifier}).

    Only the flags you pass are sent, so this is a partial (merge-patch) update.
    Passing --role replaces the whole claimed-role set.
    """
    settings: Settings = ctx.obj
    _resolve_token(settings)

    body: dict = {}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if role:
        body["role"] = role
    if home_page_url is not None:
        body["homePageURL"] = home_page_url
    if support_url is not None:
        body["supportURL"] = support_url
    if message_delivery is not None:
        body["messageDelivery"] = message_delivery
    data_service = _build_data_service(
        data_service_title,
        data_service_conforms_to,
        data_service_endpoint_url,
        data_service_oauth_issuer,
    )
    if data_service is not None:
        body["dataService"] = data_service

    if not body:
        typer.secho("Nothing to update: pass at least one field.", fg="red", err=True)
        raise typer.Exit(2)

    _emit(
        settings,
        _call(settings, "PATCH", f"/members/applications/{identifier}", body=body),
    )


@apps_app.command("delete")
def apps_delete(
    ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Application identifier."),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm deletion (required; this is destructive)."
    ),
) -> None:
    """Delete an application (DELETE /members/applications/{identifier}).

    The API refuses (409) if the application still has certificates.
    """
    settings: Settings = ctx.obj
    if not yes:
        typer.secho("Refusing to delete without --yes.", fg="red", err=True)
        raise typer.Exit(2)
    _resolve_token(settings)

    _call(settings, "DELETE", f"/members/applications/{identifier}")
    if settings.output_json:
        _emit(settings, {"deleted": identifier})
    else:
        typer.secho(f"Deleted application {identifier}.", fg="green")


@ca_app.command("download")
def ca_download(
    ctx: typer.Context,
    ca_type: str = typer.Argument(
        ..., help="Which CA bundle to download: client or signing."
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path to write the ZIP to (default: the server's filename, in the current directory).",
    ),
) -> None:
    """Download a CA certificate bundle ZIP (GET /members/ca/{ca_type}/download)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)

    content, server_name = _with_api_errors(
        lambda: client.download(settings, f"/members/ca/{quote(ca_type)}/download")
    )
    dest = output or server_name or f"directory-{ca_type}-certificates.zip"
    with open(dest, "wb") as handle:
        handle.write(content)

    if settings.output_json:
        _emit(settings, {"downloaded": dest, "bytes": len(content)})
    else:
        typer.secho(f"Saved {len(content)} bytes to {dest}", fg="green")


def _write_file(path: str, data: bytes, *, private: bool = False) -> None:
    with open(path, "wb") as handle:
        handle.write(data)
    if private:
        os.chmod(path, 0o600)


@cert_app.command("sign")
def cert_sign(
    ctx: typer.Context,
    application_identifier: str = typer.Argument(
        ..., help="Identifier of the application to sign the certificate for."
    ),
    cert_type: str = typer.Argument(..., help="Certificate type: client or signing."),
    csr: Optional[str] = typer.Option(
        None,
        "--csr",
        help="Path to an existing CSR PEM. If omitted, a key and CSR are generated locally.",
    ),
    name: Optional[str] = typer.Option(None, help="Optional label for the certificate."),
    key_out: Optional[str] = typer.Option(
        None,
        "--key-out",
        help="Where to write the generated private key (default {app}-{type}-key.pem). Ignored with --csr.",
    ),
    cert_out: Optional[str] = typer.Option(
        None,
        "--cert-out",
        help="Where to write the signed certificate (default {app}-{type}-cert.pem).",
    ),
) -> None:
    """Sign a certificate for an application.

    Without --csr, a P-256 key and CSR are generated locally and the key is written to
    disk (mode 0600). The CA forces the certificate subject regardless of the CSR.
    """
    settings: Settings = ctx.obj
    _resolve_token(settings)

    generated_key = None
    if csr is not None:
        with open(csr, "r") as handle:
            csr_pem = handle.read()
    else:
        generated_key, csr_pem = generate_key_and_csr()

    body: dict = {"csr": csr_pem}
    if name is not None:
        body["name"] = name
    result = _call(
        settings,
        "POST",
        f"/members/applications/{quote(application_identifier)}"
        f"/certificates/{quote(cert_type)}/sign",
        body=body,
    )

    saved: dict = {}
    if generated_key is not None:
        key_path = key_out or f"{application_identifier}-{cert_type}-key.pem"
        _write_file(key_path, generated_key, private=True)
        saved["key"] = key_path
    pem = result.get("certificate")
    if pem:
        cert_path = cert_out or f"{application_identifier}-{cert_type}-cert.pem"
        _write_file(cert_path, pem.encode())
        saved["certificate"] = cert_path

    if settings.output_json:
        _emit(settings, {**result, "saved": saved})
    else:
        typer.secho(
            f"Signed certificate {result.get('id')} ({cert_type}).", fg="green"
        )
        for label, path in saved.items():
            typer.secho(f"  {label}: {path}", fg="green")


@cert_app.command("download")
def cert_download(
    ctx: typer.Context,
    certificate_id: str = typer.Argument(..., help="Certificate id."),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path to write the PEM to (default: the server's filename, in the current directory).",
    ),
) -> None:
    """Download a signed certificate PEM (GET /members/certificates/{id}/download)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)

    content, server_name = _with_api_errors(
        lambda: client.download(
            settings, f"/members/certificates/{quote(certificate_id)}/download"
        )
    )
    dest = output or server_name or f"{certificate_id}.pem"
    _write_file(dest, content)

    if settings.output_json:
        _emit(settings, {"downloaded": dest, "bytes": len(content)})
    else:
        typer.secho(f"Saved {len(content)} bytes to {dest}", fg="green")


@cert_app.command("revoke")
def cert_revoke(
    ctx: typer.Context,
    certificate_id: str = typer.Argument(..., help="Certificate id."),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm revocation (required; this is destructive)."
    ),
) -> None:
    """Revoke a certificate (POST /members/certificates/{id}/revoke)."""
    settings: Settings = ctx.obj
    if not yes:
        typer.secho("Refusing to revoke without --yes.", fg="red", err=True)
        raise typer.Exit(2)
    _resolve_token(settings)

    _emit(
        settings,
        _call(
            settings,
            "POST",
            f"/members/certificates/{quote(certificate_id)}/revoke",
        ),
    )


def _officer(
    name: str, email: Optional[str], phone: Optional[str], *, label: str
) -> dict:
    """Build a trust-framework officer block; it must carry an email or a phone."""
    if email is None and phone is None:
        typer.secho(
            f"--{label}-officer-email or --{label}-officer-phone is required.",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    officer: dict = {"name": name}
    if email is not None:
        officer["email"] = email
    if phone is not None:
        officer["phone"] = phone
    return officer


@admin_app.command("create-org")
def admin_create_org(
    ctx: typer.Context,
    legal_name: str = typer.Option(..., "--legal-name", help="Registered legal name."),
    email: str = typer.Option(..., help="Organisation contact email."),
    street_address: str = typer.Option(..., "--street-address"),
    locality: str = typer.Option(..., help="City / locality."),
    postal_code: str = typer.Option(..., "--postal-code"),
    company_number: str = typer.Option(..., "--company-number"),
    role: str = typer.Option(..., help="Role slug within the environment's scheme."),
    data_officer_name: str = typer.Option(..., "--data-officer-name"),
    licence_officer_name: str = typer.Option(..., "--licence-officer-name"),
    region: Optional[str] = typer.Option(None),
    effective_date: Optional[str] = typer.Option(
        None, "--effective-date", help="ISO date (YYYY-MM-DD); defaults to today."
    ),
    data_officer_email: Optional[str] = typer.Option(None, "--data-officer-email"),
    data_officer_phone: Optional[str] = typer.Option(None, "--data-officer-phone"),
    licence_officer_email: Optional[str] = typer.Option(None, "--licence-officer-email"),
    licence_officer_phone: Optional[str] = typer.Option(None, "--licence-officer-phone"),
) -> None:
    """Onboard a new organisation (POST /admin/organizations).

    Creates the organisation, its scheme membership + role and the officer contacts. The
    scheme is fixed per environment; add owners afterwards with `admin add-member`.
    """
    settings: Settings = ctx.obj
    _resolve_token(settings)

    body: dict = {
        "legalName": legal_name,
        "email": email,
        "streetAddress": street_address,
        "locality": locality,
        "postalCode": postal_code,
        "companyNumber": company_number,
        "role": role,
        "dataOfficer": _officer(
            data_officer_name, data_officer_email, data_officer_phone, label="data"
        ),
        "licenceOfficer": _officer(
            licence_officer_name,
            licence_officer_email,
            licence_officer_phone,
            label="licence",
        ),
    }
    if region is not None:
        body["region"] = region
    if effective_date is not None:
        body["effectiveDate"] = effective_date

    _emit(settings, _call(settings, "POST", "/admin/organizations", body=body))


@admin_app.command("add-member")
def admin_add_member(
    ctx: typer.Context,
    organization_identifier: str = typer.Argument(
        ..., help="Identifier of the organisation to add the user to."
    ),
    email: str = typer.Option(..., help="Email of the user to add."),
) -> None:
    """Add a user to an organisation and provision their Cognito login
    (POST /admin/organizations/{identifier}/members)."""
    settings: Settings = ctx.obj
    _resolve_token(settings)
    _emit(
        settings,
        _call(
            settings,
            "POST",
            f"/admin/organizations/{quote(organization_identifier)}/members",
            body={"email": email},
        ),
    )
