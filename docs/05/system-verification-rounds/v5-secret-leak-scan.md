# V5 — Committed-secret leak scan (direction D-security, broadening)

- Date: 2026-06-14
- Branch: task/verify-v5-secret-scan
- Non-duplication: no task-brief covers secret/credential-leak scanning; distinct
  from V2 (dependency CVEs) and from others' BFF auth-FLOW work (auth-facade).

## Plan
Verify no real credentials are committed to the repo (a security gap orthogonal to
the dependency CVEs of V2 and the auth-flow hardening others are doing).

## Verification & result
Added `scripts/audit_secret_leak.sh`: greps tracked files for high-signal secret
assignments, filtering the false-positive classes confirmed here:
- code variable references (`getenv`, `environ`, `_configured_value`, `*.access_token`,
  `requires_confirm_token`, JWT encode helpers, ...);
- docs/examples/tests/.env files;
- self-describing seed/evidence placeholders, e.g. `"broker_api_key":
  "pantheon-prod-broker-api-key"` and `"secret_key": "pantheon-prod-shioaji-secret-key"`
  (the value IS its own description — a template placeholder, not a real key).

Result: **OK — no committed real-looking secrets** on origin/dev. The seed/evidence
files (scripts/seed_ep5_execution_secrets.py, datasource-smoke fixtures) contain only
descriptive placeholder strings; the broker is mock/disabled so they are never live keys.

## Deliverable
A reusable stopgap secret-scan gate (exit 1 on a real-looking hit). Nothing to fix
this round.

## Follow-ups
- Adopt gitleaks/trufflehog in CI for thorough entropy-based detection (the grep gate
  is a stopgap, not a replacement).
- Wire the gate into run-acceptance.sh once V3 (#1544) merges.
