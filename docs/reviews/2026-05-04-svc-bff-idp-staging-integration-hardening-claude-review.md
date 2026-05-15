# Review: SVC-BFF-IDP-STAGING-INTEGRATION-HARDENING

Reviewer: Claude
Date: 2026-05-04
Status: approved

## Summary

Codex2 implemented the BFF OIDC/JWKS staging IdP integration hardening. The
delivery is complete, well-tested, and meets all acceptance criteria.

## Acceptance Criteria Review

### 1. OIDC discovery and JWKS rotation error policy are tested ✅

Covered by `TestExtractIdentityJwks` in `test_bff_auth_facade.py`:
- `test_jwks_fetch_failure_returns_generic_401` — JWKS fetch error returns a
  sanitized 401; URI and internal code not exposed.
- `test_jwks_no_matching_key_returns_generic_401` — JWKS_NO_MATCHING_KEY code
  not leaked.
- `test_oidc_discovery_failure_returns_generic_401` — discovery URL and
  OIDC_DISCOVERY_FAILED code not leaked.
- `test_jwks_cache_miss_refreshes_for_rotated_kid` — stale cache miss forces
  one JWKS refresh before rejection; key rotation handled gracefully.
- `test_jwks_cache_used_on_second_call` — TTL cache avoids redundant fetches.
- `test_oidc_discovery_url_resolves_jwks_uri` — discovery metadata path
  resolves jwks_uri and delegates to JWKS verification.

### 2. Claim role and MFA mapping configurable; negative tests present ✅

Role mapping:
- `test_role_and_mfa_claim_mapping_can_be_strict` — strict mode with groups
  claim maps correctly.
- `test_strict_role_map_rejects_unmapped_group` — unit test: unmapped IdP
  group yields empty roles.
- `test_strict_role_map_unmapped_group_denies_admin_write` — HTTP-level test:
  unmapped group 403 on POST /api/v1/settings with role_check precondition.
- `test_default_role_fallback_when_no_role_claim` — default role applied when
  no claim present.

MFA mapping (negative tests):
- `test_mfa_required_rejects_unaccepted_idp_mfa_claim` — `sms` (not in
  accepted MFA_VALUES) fails when MFA_REQUIRED=true; error body contains
  MFA_REQUIRED code.
- `test_mfa_not_verified_without_any_claim` — baseline: no amr/acr/mfa claim
  and no header → mfa_verified=False.
- `test_mfa_from_amr_claim_default_paths`, `test_mfa_from_acr_claim`,
  `test_mfa_from_boolean_mfa_verified_claim` — positive paths for all default
  claim shapes.

### 3. Staging env example requires real issuer, audience, and JWKS/discovery URL ✅

`test_prod_control_env_requires_staging_oidc_idp_posture` in
`test_bff_oidc_staging_env_contract.py` asserts:
- `PANTHEON_BFF_AUTH_STUB=false`, `PANTHEON_BFF_AUTH_MODE=strict`
- `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE` are all blank (HS256 disabled)
- `OIDC_ISSUER` starts with `https://`, `OIDC_AUDIENCE` non-empty
- `JWKS_URI` or `OIDC_DISCOVERY_URL` must be set
- Full staging role map with four groups; `ROLE_MAP_MODE=strict`;
  `DEFAULT_ROLE=viewer`
- `MFA_REQUIRED=true`; claims ⊇ `{amr, acr, mfa_verified}`; values ⊇
  `{mfa, otp, totp, webauthn}`

`env/prod-control.env.example` satisfies all assertions with example values
and a clear operator comment to replace before deploy.

### 4. Auth stub remains dev/test only ✅

- `prod-control.env.example`: `PANTHEON_BFF_AUTH_STUB=false`
- Compose default: `${PANTHEON_BFF_AUTH_STUB:-false}` — stub off by default
- `test_stub_token_rejected_in_jwks_mode` and
  `test_jwt_stub_token_rejected_in_jwt_mode` verify colon-format stub tokens
  are rejected in both JWKS and JWT modes.
- `test_control_compose_forwards_bff_idp_env_with_stub_disabled_by_default`
  verifies the compose env forwarding block.

## Verification

Commands from the handoff, reproduced and confirmed:

```
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py \
  services/control-plane/bff/test_bff_oidc_staging_env_contract.py -q
→ 68 passed

python3 -m pytest services/control-plane/bff/test_staging_read_store_cutoff_contract.py -q
→ 4 passed

docker compose --env-file env/prod-control.env.example \
  -f docker-compose.control.yml config --quiet
→ renders without error

git diff --check
→ clean
```

## Notes

No issues found. The deployment doc (`docs/deployment/bff-oidc-staging-auth.md`)
is clear, covers the claim-mapping examples, HS256 fallback posture, and
staging smoke commands.

The compose block exposes all required BFF IdP env vars with safe defaults
(`stub=false`, `mode=strict`). The env example file is unambiguous that JWT
secret must remain blank for OIDC staging posture.
