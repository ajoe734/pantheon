# BFF OIDC Staging Auth

Status date: 2026-05-04

This note defines the staging-live IdP posture for the operator BFF. It is a
deployment record for `docker-compose.control.yml` and
`env/prod-control.env.example`; it does not change canonical auth policy.

## Runtime Behavior

- `PANTHEON_BFF_AUTH_STUB=false` keeps colon-format stub tokens disabled.
- `PANTHEON_BFF_AUTH_MODE=strict` requires JWT-shaped bearer tokens.
- OIDC/JWKS validation uses RS256 or ES256 only.
- Configure one of:
  - `PANTHEON_BFF_OIDC_DISCOVERY_URL`, which resolves `jwks_uri` from OIDC
    metadata.
  - `PANTHEON_BFF_JWKS_URI`, when discovery is not available and the issuer's
    JWKS endpoint is documented directly.
- The checked-in env example shows the required staging shape. Before a VM1
  staging-live deploy, replace the example IdP host with the actual issuer,
  audience, and either discovery URL or direct JWKS URI.
- `PANTHEON_BFF_OIDC_ISSUER` and `PANTHEON_BFF_OIDC_AUDIENCE` must match the
  token `iss` and `aud` claims. If `OIDC_ISSUER` is blank and discovery is
  used, the discovery metadata `issuer` is used.
- JWKS keys are cached for five minutes. A token `kid` cache miss forces one
  JWKS refresh before the request is rejected, which handles normal IdP key
  rotation without waiting for TTL expiry.
- JWKS fetch, no-matching-key, invalid-key, missing crypto, and discovery
  failures are returned to BFF callers as a generic unverifiable-token 401.
  Internal IdP URLs and low-level auth codes are not exposed in the response.

## Claim Mapping

Staging should use explicit claim mapping instead of accepting arbitrary IdP
group names as Pantheon roles:

```env
PANTHEON_BFF_ROLE_CLAIMS=groups,roles
PANTHEON_BFF_ROLE_MAP=pantheon-staging-admins=admin;pantheon-staging-operators=operator;pantheon-staging-reviewers=reviewer;pantheon-staging-approvers=approver
PANTHEON_BFF_ROLE_MAP_MODE=strict
PANTHEON_BFF_DEFAULT_ROLE=viewer
```

`ROLE_CLAIMS` supports dot paths, for example `realm_access.roles`. In strict
mode, only values present in `ROLE_MAP` become Pantheon roles. Unmapped IdP
groups do not become operator/admin/reviewer/approver roles.

MFA can be satisfied by the `X-MFA-Token` header or by IdP claims:

```env
PANTHEON_BFF_MFA_REQUIRED=true
PANTHEON_BFF_MFA_CLAIMS=amr,acr,mfa,mfa_verified
PANTHEON_BFF_MFA_VALUES=mfa,otp,totp,webauthn,true
```

For example, an `amr` claim containing `mfa` or `webauthn` marks the BFF
identity as MFA-verified and satisfies admin-action MFA checks.

## HS256 Fallback

HS256 remains available only as the explicit dev fallback and the dev-login
issuer for CI/Lovable short-lived JWTs:

```env
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_JWT_SECRET=<non-staging-dev-secret>
PANTHEON_BFF_JWT_ISSUER=pantheon-dev
PANTHEON_BFF_JWT_AUDIENCE=bff-operators
PANTHEON_BFF_JWKS_URI=
PANTHEON_BFF_OIDC_DISCOVERY_URL=
PANTHEON_BFF_OIDC_CLIENT_ID=<dev-client-id>
PANTHEON_BFF_OIDC_CLIENT_SECRET=<dev-client-secret>
PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS=1800
```

Do not set `PANTHEON_BFF_JWT_SECRET` in staging-live when OIDC discovery or a
JWKS URI is configured.

The dev BFF exposes `POST /bff/auth/dev-login` for client-credentials exchange:

```json
{
  "grant_type": "client_credentials",
  "client_id": "<dev-client-id>",
  "client_secret": "<dev-client-secret>"
}
```

The response contains `access_token`, `token_type=bearer`, `expires_in`,
`issued_at`, and `expires_at`. The token is signed with the dev HS256 secret,
uses `PANTHEON_BFF_JWT_ISSUER` / `PANTHEON_BFF_JWT_AUDIENCE`, and is limited to
5 minutes through 1 hour. Revocation is by rotating
`PANTHEON_BFF_OIDC_CLIENT_SECRET`, disabling the endpoint, or rotating the dev
JWT signing secret. The endpoint must stay disabled on staging-live/live/prod.

## Staging Smoke

Render the VM1 compose contract:

```bash
docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml config --quiet
```

Run focused auth coverage:

```bash
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
python3 -m pytest services/control-plane/bff/test_bff_oidc_staging_env_contract.py -q
```

The focused tests cover OIDC discovery, JWKS validation, cache hit, forced
refresh on `kid` miss, sanitized auth errors, strict role mapping, negative MFA
claim mapping, staging env/compose IdP posture, stub-disabled defaults, and
HS256 fallback.
