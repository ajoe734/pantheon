# GCP Identity Platform authentication

Pantheon dev browser authentication is owned by GCP Identity Platform in
the project that `nonprod-deploy.yml` passes to the BFF as
`DEV_BFF_OIDC_AUDIENCE`; its workflow fallback is the dev project of
[§ 3.1](vm-dev-staging-prod-management-plan.md#31-dev) (value not copied here; `<dev-project>` below). Supabase is not an
authentication dependency.

## Runtime contract

- Frontend: Firebase Web SDK against GCP Identity Platform, with
  `browserSessionPersistence` only.
- First factor: email/password or Google OAuth through the same Firebase
  Identity Platform project.
- Account recovery: Identity Platform verification and password-reset email.
- Second factor: TOTP authenticator.
- BFF verification:
  - JWKS:
    `https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com`
  - issuer:
    `https://securetoken.google.com/<dev-project>`
  - audience: `<dev-project>`
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
- Google sign-in enabled when the dev hosted functional browser path uses
  Google OAuth. The OAuth provider must be registered in the same Identity
  Platform project; do not introduce a second Supabase or OIDC user store.
- Password policy enforced with minimum 12 and maximum 128 characters,
  including lower-case, upper-case, numeric, and non-alphanumeric characters.
- TOTP MFA enabled.
- Authorized domains:
  - `<dev-project>.firebaseapp.com`
  - `<dev-project>.web.app`
  - the `DEV_FE_PUBLIC_HOST` variable
  - `localhost`

The hosted functional browser path uses the Firebase Web SDK's short-lived
Google ID token exactly like an email/password ID token. The BFF validates the
token against the same issuer/audience/JWKS configuration; no OAuth client
secret is compiled into the frontend.

Frontend repository variables:

```text
VITE_GCP_IDENTITY_API_KEY
VITE_GCP_IDENTITY_PROJECT_ID=<dev-project>
VITE_GCP_IDENTITY_AUTH_DOMAIN=<dev-project>.firebaseapp.com
```

BFF repository variables:

```text
DEV_BFF_JWKS_URI=https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com
DEV_BFF_OIDC_ISSUER=https://securetoken.google.com/<dev-project>
DEV_BFF_OIDC_AUDIENCE=<dev-project>
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
  --project-id <dev-project> \
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
