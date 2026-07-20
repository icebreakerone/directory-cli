# Directory CLI

Command-line client for the IB1 Directory member API. Built to make the API easy to
exercise as it grows, and to be drivable by scripts and code agents (machine-readable
output, real exit codes, no prompts).

Authenticate either by **logging in** (browser, authorization-code + PKCE, token cached in
the OS keyring) or by **pasting a token** (`--token` / `DIRECTORY_TOKEN`) for short runs and
CI.

## Install

```bash
pip install ib1-directory-cli
```

For local development from a clone:

```bash
pip install -e ".[dev]"   # or: uv sync --extra dev
```

This installs a `directory` command.

## Configuration

| Option      | Env var             | Default                 |
| ----------- | ------------------- | ----------------------- |
| `--api-url` | `DIRECTORY_API_URL` | `http://localhost:8000` |
| `--token`   | `DIRECTORY_TOKEN`   | (none)                  |
| `--json`    |                     | pretty-printed          |

Login uses these (the public Cognito client is environment-specific):

| Env var                       | Meaning                                                   |
| ----------------------------- | --------------------------------------------------------- |
| `DIRECTORY_COGNITO_DOMAIN`    | Hosted UI base URL, e.g. `https://<prefix>.auth.<region>.amazoncognito.com` |
| `DIRECTORY_COGNITO_CLIENT_ID` | The public (no-secret) CLI app client id                  |
| `DIRECTORY_OAUTH_SCOPES`      | Default `openid email`                                    |
| `DIRECTORY_REDIRECT_PORT`     | Default `8400` (must match the client's registered callback) |

## Login

```bash
directory login     # opens a browser, caches the token in your OS keyring
directory logout    # clears the cached token
directory token     # prints a current id token (refreshing if needed)
```

After `directory login`, `me get` / `me update` use the cached token automatically. Token
precedence is `--token` then `DIRECTORY_TOKEN` then the keyring cache.

`directory token` is the bridge for agents/CI that can't open a browser: a human runs it and
passes the value as `DIRECTORY_TOKEN`. The token is short-lived, so this suits short runs.

## Usage

```bash
# Read your organisation
directory --token "$ACCESS_TOKEN" me get

# Same, token from the environment, compact JSON for piping
export DIRECTORY_TOKEN=...
directory --json me get | jq .identifier

# Partial update (only the flags you pass are sent — a merge-patch)
directory me update --street-address "1 New Road" --email ops@acme.example
directory me update --country GB --postal-code "AB1 2CD"
```

Editable fields: `--email`, `--street-address`, `--locality`, `--region`, `--state`,
`--postal-code`, `--country`, `--privacy-policy`, `--data-protection-url`.

### Applications

```bash
# List your applications (optionally filter by scheme short name)
directory apps list
directory apps list --scheme perseus

# Read one application by identifier
directory apps get abc12345

# Create an application under a scheme (roles are repeatable role identifier URLs)
directory apps create --scheme perseus --title "My App" \
  --role https://registry.trust.ib1.org/scheme/perseus/role/data-provider \
  --home-page-url https://app.example.com

# Create with a data service (title, conforms-to and endpoint-url go together)
directory apps create --scheme perseus --title "My App" \
  --data-service-title "My Feed" \
  --data-service-conforms-to https://standard.example.com \
  --data-service-endpoint-url https://api.example.com

# Partial update (only the flags you pass are sent; --role replaces the whole set)
directory apps update abc12345 --title "Renamed" --support-url https://support.example.com

# Delete (destructive, so --yes is required; the API refuses with 409 if certificates exist)
directory apps delete abc12345 --yes
```

The `publisher` on a data service is set by the server to your organisation, so there is no
flag for it. Application create/update accept `--description`, `--home-page-url`,
`--support-url`, `--message-delivery`, `--role`, and the four `--data-service-*` flags.

### Administration

Admin commands require your Cognito account to be in the directory admin group; other users
get a 403.

```bash
# Onboard a new organisation (creates the org, its scheme membership + role, and the
# officer contacts). The scheme is fixed per environment; the role is a slug within it.
directory admin create-org \
  --legal-name "Acme Ltd" --email contact@acme.example \
  --street-address "1 Main St" --locality London --postal-code "AB1 2CD" \
  --company-number 12345678 --role energy-data-provider \
  --data-officer-name "Dana" --data-officer-email dana@acme.example \
  --licence-officer-name "Lee" --licence-officer-phone "+441234567890"

# Add a user to an organisation and send them a Cognito invite (they become an owner)
directory admin add-member <organization-identifier> --email new.owner@acme.example
```

Each officer needs an email or a phone. `--region` and `--effective-date` (ISO `YYYY-MM-DD`,
default today) are optional. Onboarding = `create-org`, then `add-member` for each owner.
### Certificates

```bash
# Sign a certificate for an application. With no --csr, a private key and CSR are
# generated locally; the key is written to disk (mode 0600) and the signed cert saved.
directory cert sign abc12345 client
# → my-app writes abc12345-client-key.pem and abc12345-client-cert.pem, prints the cert id

# Use your own CSR instead of generating one (no key is written):
directory cert sign abc12345 signing --csr my.csr --cert-out signing.pem

# Download a certificate by id (default filename comes from the server)
directory cert download <certificate-id> -o cert.pem

# Revoke a certificate (destructive, so --yes is required; this is a soft revoke)
directory cert revoke <certificate-id> --yes
```

`cert sign` takes the application identifier and the type (`client` or `signing`). The CA
forces the certificate subject to your organisation regardless of the CSR, so a generated
CSR's subject does not matter. Download the CA root/intermediate bundle with
`directory ca download <client|signing>`.

## Exit codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | Success                                              |
| 1    | API or transport error (4xx/5xx, connection failure) |
| 2    | Usage error (no token, update with no fields, delete without `--yes`) |

## Prerequisites for login (one-time, out of this repo)

Interactive login needs a **public** Cognito app client on the existing user pool:

- no client secret
- authorization-code grant with PKCE
- callback URL `http://localhost:8400/callback` (the exact port must match `DIRECTORY_REDIRECT_PORT`)
- scopes `openid email`

Its client id must also be added to the API's `COGNITO_ALLOWED_CLIENT_IDS` so the API accepts
tokens it issues. Both are deploy/infra steps (AWS + the deployments repo), not part of the CLI.

## Tests

```bash
pip install -e ".[dev]"   # or: uv sync --extra dev
pytest                     # or: uv run pytest
```
