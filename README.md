# Directory CLI

Command-line client for the IB1 Directory member API. Built to make the API easy to
exercise as it grows, and to be drivable by scripts and code agents (machine-readable
output, real exit codes, no prompts).

Authenticate either by **logging in** (browser, authorization-code + PKCE, token cached in
the OS keyring) or by **pasting a token** (`--token` / `DIRECTORY_TOKEN`) for short runs and
CI.

## Install

```bash
cd cli
pip install -e .          # or: pip install -e ".[dev]" for tests
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
directory token     # prints a current access token (refreshing if needed)
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

## Exit codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | Success                                              |
| 1    | API or transport error (4xx/5xx, connection failure) |
| 2    | Usage error (no token, or update with no fields)     |

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
cd cli
pip install -e ".[dev]"
pytest
```
