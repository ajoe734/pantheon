# FE-INT-GATE-A11 Review - Codex

Reviewer: Codex
Reviewed at: 2026-05-14T04:00:00Z
Task: FE-INT-GATE-A11

## Scope

Reviewed `execute-plans/scripts/probe-bff-authenticated-live.mjs` for the A11 auth probe envelope correction.

## Findings

No blocking findings.

The implementation now checks the canonical A11 list shape with top-level `items` and `page_info.total` in `isListEnvelope`, keeps `/bff/me` on a separate user/tenant/capabilities validator, and documents the live list envelope plus explicit route DTO variants in the emitted evidence note.

The write/precondition probe also avoids the approval route's prior permissive success path by sending an invalid payload and expecting a typed 4xx error envelope.

## Verification

Commands run:

```bash
node --check execute-plans/scripts/probe-bff-authenticated-live.mjs
```

```bash
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io PANTHEON_BFF_SMOKE_BEARER_TOKEN=pantheon-dev-browser:reviewer PANTHEON_AUDIT_OUT_DIR=/tmp/pantheon-fe-int-gate-a11-review-codex node execute-plans/scripts/probe-bff-authenticated-live.mjs
```

Result: Passed 31/31.

```bash
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io PANTHEON_BFF_SMOKE_BEARER_TOKEN=pantheon-dev-browser:reviewer PANTHEON_AUDIT_OUT_DIR=/tmp/pantheon-fe-int-gate-a11-review-codex-execute-plans node scripts/probe-bff-authenticated-live.mjs
```

Run from `execute-plans/`.
Result: Passed 31/31.

PR CI note: `gh pr list --repo ajoe734/pantheon --head backend-dev-publish-20260429 --state open --json number,headRefName,title,url,statusCheckRollup` returned `[]`, so no open PR CI auth_smoke rerun was available from this checkout.
