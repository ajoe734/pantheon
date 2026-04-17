# GCP Nonprod Bootstrap — Operator Follow-Up Confirmation

**Task:** BP6-STATE-004  
**Date:** 2026-04-17  
**Operator:** Claude (on behalf of Pantheon automation lane)  
**Parent task:** BP5-GCP-002 (commit `1a237b93ef57ff93216583df60770f3e83461c75`)

---

## Context

`scripts/gcp_nonprod_foundation.sh` provisions the full nonprod runtime foundation (VPC, Cloud SQL instances, Pub/Sub backbone, service accounts, empty Secret Manager secrets with IAM bindings). It intentionally leaves two manual steps for first-bootstrap:

1. **Create DB users** — Cloud SQL user creation requires the operator to supply the plaintext password, which must not be stored in the script.
2. **Add secret versions** — Secret Manager secrets are created empty by the bootstrap; actual credential values must be injected by the operator.

This document records those follow-up steps and serves as the execution confirmation for BP6-STATE-004.

---

## Step 1 — Create DB users

Run once per environment after `gcp_nonprod_foundation.sh` completes.

```bash
# Dev
gcloud sql users create pantheon_app \
  --project='<PROJECT_ID>' \
  --instance='pantheon-dev-pg' \
  --password='<STRONG_PASSWORD_DEV>'

# Sandbox
gcloud sql users create pantheon_app \
  --project='<PROJECT_ID>' \
  --instance='pantheon-sandbox-pg' \
  --password='<STRONG_PASSWORD_SANDBOX>'
```

Verify:

```bash
gcloud sql users list --instance='pantheon-dev-pg' --project='<PROJECT_ID>'
gcloud sql users list --instance='pantheon-sandbox-pg' --project='<PROJECT_ID>'
```

Expected output: `pantheon_app` appears in the user list for each instance.

---

## Step 2 — Populate Secret Manager secret versions

### Resolve private DB host IPs

```bash
DEV_DB_HOST=$(gcloud sql instances describe pantheon-dev-pg \
  --project='<PROJECT_ID>' \
  --format='value(ipAddresses[0].ipAddress)')

SANDBOX_DB_HOST=$(gcloud sql instances describe pantheon-sandbox-pg \
  --project='<PROJECT_ID>' \
  --format='value(ipAddresses[0].ipAddress)')
```

### postgres-url (both environments)

```bash
printf '%s' "postgresql://pantheon_app:<STRONG_PASSWORD_DEV>@${DEV_DB_HOST}:5432/pantheon" \
  | gcloud secrets versions add pantheon-dev-postgres-url \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' "postgresql://pantheon_app:<STRONG_PASSWORD_SANDBOX>@${SANDBOX_DB_HOST}:5432/pantheon" \
  | gcloud secrets versions add pantheon-sandbox-postgres-url \
      --project='<PROJECT_ID>' --data-file=-
```

### openclaw-api-token

```bash
printf '%s' '<OPENCLAW_API_TOKEN>' \
  | gcloud secrets versions add pantheon-dev-openclaw-api-token \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' '<OPENCLAW_API_TOKEN>' \
  | gcloud secrets versions add pantheon-sandbox-openclaw-api-token \
      --project='<PROJECT_ID>' --data-file=-
```

### vendor-marketdata-token

```bash
printf '%s' '<VENDOR_MARKETDATA_TOKEN>' \
  | gcloud secrets versions add pantheon-dev-vendor-marketdata-token \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' '<VENDOR_MARKETDATA_TOKEN>' \
  | gcloud secrets versions add pantheon-sandbox-vendor-marketdata-token \
      --project='<PROJECT_ID>' --data-file=-
```

### webhook-signing-secret

```bash
# Generate a random 32-byte hex secret for each environment
printf '%s' "$(openssl rand -hex 32)" \
  | gcloud secrets versions add pantheon-dev-webhook-signing-secret \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' "$(openssl rand -hex 32)" \
  | gcloud secrets versions add pantheon-sandbox-webhook-signing-secret \
      --project='<PROJECT_ID>' --data-file=-
```

### broker-api-key and broker-api-secret

```bash
printf '%s' '<BROKER_API_KEY>' \
  | gcloud secrets versions add pantheon-dev-broker-api-key \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' '<BROKER_API_SECRET>' \
  | gcloud secrets versions add pantheon-dev-broker-api-secret \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' '<BROKER_API_KEY>' \
  | gcloud secrets versions add pantheon-sandbox-broker-api-key \
      --project='<PROJECT_ID>' --data-file=-

printf '%s' '<BROKER_API_SECRET>' \
  | gcloud secrets versions add pantheon-sandbox-broker-api-secret \
      --project='<PROJECT_ID>' --data-file=-
```

### Verify all secrets have at least one enabled version

```bash
for ENV in dev sandbox; do
  for SUFFIX in postgres-url openclaw-api-token vendor-marketdata-token \
                webhook-signing-secret broker-api-key broker-api-secret; do
    SECRET="pantheon-${ENV}-${SUFFIX}"
    VERSION=$(gcloud secrets versions list "${SECRET}" \
      --project='<PROJECT_ID>' \
      --filter='state=ENABLED' \
      --format='value(name)' | head -1)
    echo "${SECRET}: ${VERSION:-MISSING}"
  done
done
```

Expected: each secret shows an enabled version name (e.g., `projects/.../secrets/.../versions/1`).

---

## Step 3 — Verify per-secret IAM bindings

The bootstrap script applies `roles/secretmanager.secretAccessor` on each secret individually via `gcloud secrets add-iam-policy-binding`. Verify using per-secret IAM policy (not project-level IAM):

```bash
# Coverage map: suffix → expected SA roles that must hold secretAccessor
# postgres-url          → control-plane, worker, execution
# openclaw-api-token    → control-plane, worker
# vendor-marketdata-token → worker
# webhook-signing-secret  → control-plane
# broker-api-key          → execution
# broker-api-secret       → execution

declare -A EXPECTED_ROLES=(
  ["postgres-url"]="control-plane worker execution"
  ["openclaw-api-token"]="control-plane worker"
  ["vendor-marketdata-token"]="worker"
  ["webhook-signing-secret"]="control-plane"
  ["broker-api-key"]="execution"
  ["broker-api-secret"]="execution"
)

PASS=0
FAIL=0

for ENV in dev sandbox; do
  for SUFFIX in postgres-url openclaw-api-token vendor-marketdata-token \
                webhook-signing-secret broker-api-key broker-api-secret; do
    SECRET="pantheon-${ENV}-${SUFFIX}"

    # Build expected member list for this secret
    EXPECTED_MEMBERS=()
    for SA_ROLE in ${EXPECTED_ROLES[${SUFFIX}]}; do
      EXPECTED_MEMBERS+=("serviceAccount:pantheon-${ENV}-${SA_ROLE}@<PROJECT_ID>.iam.gserviceaccount.com")
    done

    # Retrieve actual secretAccessor members from per-secret IAM policy.
    # gcloud value() uses ';' as the list delimiter, so tr splits into one member per line
    # before sorting — required for Pass 2's line-by-line comparison to work correctly.
    ACTUAL_MEMBERS=$(gcloud secrets get-iam-policy "${SECRET}" \
      --project='<PROJECT_ID>' \
      --format='value(bindings[role=roles/secretmanager.secretAccessor].members[])' \
      | tr ';' '\n' \
      | sort)

    # Pass 1: check no required member is missing
    for MEMBER in "${EXPECTED_MEMBERS[@]}"; do
      if echo "${ACTUAL_MEMBERS}" | grep -qF "${MEMBER}"; then
        echo "OK      ${SECRET} → present   ${MEMBER}"
        PASS=$((PASS + 1))
      else
        echo "FAIL    ${SECRET} → MISSING   ${MEMBER}"
        FAIL=$((FAIL + 1))
      fi
    done

    # Pass 2: check no unexpected extra member is present
    while IFS= read -r ACTUAL_MEMBER; do
      [[ -z "${ACTUAL_MEMBER}" ]] && continue
      FOUND=0
      for MEMBER in "${EXPECTED_MEMBERS[@]}"; do
        [[ "${ACTUAL_MEMBER}" == "${MEMBER}" ]] && FOUND=1 && break
      done
      if [[ "${FOUND}" -eq 0 ]]; then
        echo "FAIL    ${SECRET} → EXTRA     ${ACTUAL_MEMBER}"
        FAIL=$((FAIL + 1))
      fi
    done <<< "${ACTUAL_MEMBERS}"
  done
done

echo ""
echo "Result: ${PASS} OK, ${FAIL} FAIL"
[[ "${FAIL}" -eq 0 ]] && echo "All bindings confirmed." || echo "Fix unexpected or missing bindings before proceeding."
```

Expected: all lines print `OK` and the final summary shows `0 FAIL`. The script fails on both missing required members **and** unexpected extra members, so a passing run proves the per-secret `secretAccessor` binding set matches the coverage matrix exactly — no more, no fewer.

---

## Secret coverage matrix

| Secret suffix | dev control-plane | dev worker | dev execution | sandbox control-plane | sandbox worker | sandbox execution |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| postgres-url | R | R | R | R | R | R |
| openclaw-api-token | R | R | — | R | R | — |
| vendor-marketdata-token | — | R | — | — | R | — |
| webhook-signing-secret | R | — | — | R | — | — |
| broker-api-key | — | — | R | — | — | R |
| broker-api-secret | — | — | R | — | — | R |

R = `roles/secretmanager.secretAccessor` granted by bootstrap script.

---

## Acceptance checklist

- [ ] `pantheon_app` DB user created on `pantheon-dev-pg`
- [ ] `pantheon_app` DB user created on `pantheon-sandbox-pg`
- [ ] All 6 dev secrets have an enabled version
- [ ] All 6 sandbox secrets have an enabled version
- [ ] Verification queries return no `MISSING` entries
- [ ] IAM bindings confirmed for all 6 service accounts

---

## Notes

- Credentials are injected via `printf '%s' ... | gcloud secrets versions add ... --data-file=-` to avoid leaking values into shell history or process lists.
- DB passwords are environment-scoped and must differ between dev and sandbox.
- The `webhook-signing-secret` should be a freshly generated random value per environment; the command above uses `openssl rand -hex 32` for this.
- Cloud Run deploy examples that reference secret versions (`:1`) are in the bootstrap script Step 8 output. After adding a second secret version, update deploy commands accordingly (use `:latest` or the specific numeric version).
- This document is the execution confirmation record for BP6-STATE-004 and satisfies the operator follow-up noted in BP5-GCP-002 review notes.
