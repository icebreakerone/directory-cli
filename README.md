# Directory CLI

Command-line client for the IB1 Directory member API. Built to make the API easy to
exercise as it grows, and to be drivable by scripts and code agents (machine-readable
output, real exit codes, no prompts).

This is the **token-paste** stage: you supply a bearer access token. Interactive login
(authorization-code + PKCE) and a keyring token cache come in a later slice.

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

## Getting a token (temporary)

Paste a current Cognito **access** token (e.g. from a
browser session or a dev script). It is short-lived, so only suitable for short runs.

## Tests

```bash
cd cli
pip install -e ".[dev]"
pytest
```
