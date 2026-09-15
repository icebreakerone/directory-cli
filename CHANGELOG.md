# Changelog

All notable changes to this project will be documented in this file.

## [v0.5.0] - 2026-09-15

### Changed

- `login` gets the Cognito hosted UI domain, client id and scopes from the API it is pointed at (`GET /.well-known/directory-cli`), so it needs only `--api-url` or `DIRECTORY_API_URL`. `DIRECTORY_COGNITO_DOMAIN`, `DIRECTORY_COGNITO_CLIENT_ID` and `DIRECTORY_OAUTH_SCOPES` still work, and now override the published values. **Login discovery requires a directory API that has this endpoint.** Against an older API, set the two Cognito variables as before
- The default API URL is now the sandbox, `https://directory.core.sandbox.trust.ib1.org`, instead of `http://localhost:8000`. Set `--api-url` or `DIRECTORY_API_URL` to use another environment or a local API
- Cached tokens are keyed by API URL rather than Cognito client id, and store the client that issued them. `logout` and `token` act on the token for the current `--api-url`. **Tokens cached by v0.4.0 are not read, so run `directory login` once after upgrading**

## [v0.4.0] - 2026-08-20

First release published to PyPI, and the first with the repository public.

### Added

- `login` and `logout`: browser sign-in using the authorization code flow with PKCE, caching the token in the OS keyring. `token` prints a current id token for piping into another tool
- `me get`, `me update` and `me orgs` for reading and editing your own organisation, and listing every organisation you own
- `apps list`, `apps get`, `apps create`, `apps update` and `apps delete` for the member application endpoints
- `ca download` to fetch a certificate authority bundle as a ZIP of the root, intermediate and combined PEMs
- `cert sign`, `cert download` and `cert revoke` for application certificates
- `admin create-org` and `admin add-member` for onboarding, which require membership of the directory admin group
- Organisation selector for users who own more than one organisation, via `--organization` or `DIRECTORY_ORGANIZATION`, sent as the `X-Organization` header
- Configuration from a `.env` file in the working directory, as well as from the environment

### Changed

- Certificate and CA download paths now use format extensions, `/members/certificates/{id}.pem` and `/members/ca/{ca_type}.zip`, matching the renamed API endpoints. **This release requires a directory API that has those endpoints**, which means IB1 Directory v3.4.0 or later
- The member API is authenticated with the Cognito id token, keyed on the email claim

### Fixed

- Recover from a macOS keychain owner-edit failure when caching the token, instead of failing the login

### Notes

`v0.3.0` was tagged on 2026-07-21 but never published to PyPI, and it predates the certificate
and CA endpoint rename. Nothing was ever released from it. `v0.4.0` is the first version
available with `pip install ib1-directory-cli`.
