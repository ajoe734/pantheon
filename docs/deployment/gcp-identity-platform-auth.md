# GCP Identity Platform authentication

Pantheon dev browser authentication is owned by GCP Identity Platform in
project `pantheon-lupin-dev-20260719`. Supabase is not an authentication
dependency.

## Runtime contract

- Frontend: Firebase Web SDK against GCP Identity Platform, with
  `browserSessionPersistence` only.
- First factor: email and password.
- Account recovery: Identity Platform verification and password-reset email.
- Second factor: TOTP authenticator.
- BFF verification:
  - JWKS:
    `https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com`
  - issuer:
    `https://securetoken.google.com/pantheon-lupin-dev-20260719`
  - audience: `pantheon-lupin-dev-20260719`
  - `email_verified=true` required.
  - `firebase.sign_in_second_factor=totp` required while dev MFA enforcement is
    enabled.
- Users without a signed role claim fail closed to the BFF `viewer` role.

The public browser API key identifies the GCP project. It is not an admin
credential. Never place a service-account key, OAuth client secret, BFF bearer,
or user password in a `VITE_*` variable.

## GCP project configuration

Identity Platform must have:

- Email/password enabled and password-required.
- Password policy enforced with minimum 12 and maximum 128 characters,
  including lower-case, upper-case, numeric, and non-alphanumeric characters.
- TOTP MFA enabled.
- Authorized domains:
  - `pantheon-lupin-dev-20260719.firebaseapp.com`
  - `pantheon-lupin-dev-20260719.web.app`
  - `pantheon-lupin-dev-fe.35.201.204.12.sslip.io`
  - `localhost`

Frontend repository variables:

```text
VITE_GCP_IDENTITY_API_KEY
VITE_GCP_IDENTITY_PROJECT_ID=pantheon-lupin-dev-20260719
VITE_GCP_IDENTITY_AUTH_DOMAIN=pantheon-lupin-dev-20260719.firebaseapp.com
```

BFF repository variables:

```text
DEV_BFF_JWKS_URI=https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com
DEV_BFF_OIDC_ISSUER=https://securetoken.google.com/pantheon-lupin-dev-20260719
DEV_BFF_OIDC_AUDIENCE=pantheon-lupin-dev-20260719
DEV_BFF_ROLE_CLAIMS=roles,role
DEV_BFF_DEFAULT_ROLE=viewer
DEV_BFF_MFA_CLAIMS=amr,acr,mfa,mfa_verified,firebase.sign_in_second_factor
DEV_BFF_MFA_VALUES=true,1,yes,mfa,otp,totp,webauthn
DEV_BFF_MFA_REQUIRED=true
DEV_BFF_REQUIRE_EMAIL_VERIFIED=true
```

`DEV_BFF_OIDC_DISCOVERY_URL`, `VITE_SUPABASE_URL`, and
`VITE_SUPABASE_PUBLISHABLE_KEY` must be absent after cutover.

## Account lifecycle

There is no shared default account or password.

1. The user creates an account on `/auth`.
2. The user verifies the email address.
3. The UI requires TOTP enrollment and a fresh sign-in.
4. The BFF admits the account as read-only `viewer`.
5. An authorized GCP operator may grant governed roles after reviewing the
   account:

```bash
python3 scripts/gcp_identity_set_roles.py \
  --email operator@example.com \
  --role operator \
  --role reviewer
```

The command uses Application Default Credentials, preserves unrelated custom
claims, and does not accept or print credentials. New claims appear after the
user signs out and signs in again.

## Acceptance

Before accepting a dev deployment:

1. Anonymous product routes redirect to `/auth`.
2. Unverified email is rejected by the BFF.
3. A password-only token is rejected while MFA is required.
4. A verified TOTP token passes `/bff/me`.
5. An account without role claims receives only `viewer`.
6. The hosted frontend bundle contains GCP Identity configuration and no
   Supabase runtime module or URL.
