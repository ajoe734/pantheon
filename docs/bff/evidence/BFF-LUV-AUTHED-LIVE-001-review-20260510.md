# BFF-LUV-AUTHED-LIVE-001 Review

**Reviewer:** Claude
**Date:** 2026-05-10
**Task:** Run authenticated lupin dev BFF DTO/write smoke
**Owner:** Codex
**Status:** Approved

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| Valid operator auth path found or exact blocker recorded | Pass — HS256 JWT minted from `PANTHEON_BFF_SMOKE_JWT_SECRET`; secret not written to evidence |
| Authenticated live DTO smoke validates route families | Pass — 30/30 read probes, all required families covered |
| Approved non-capital write-flow smoke validates governed receipt envelope | Pass — 5/5 confirm-token probes, `live_capital_side_effects: false` |
| Execute-plans live/write handoff updated | Pass — gate outcomes recorded in task artifact |

## Evidence Review

Evidence file: `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json`

- **Total probes:** 37 (2 health/openapi + 30 authenticated read + 5 confirm-token write)
- **Passed:** 37
- **Failed:** 0
- **Live capital side effects:** false

### Route Family Coverage

All required families from the task scope are covered:

- session (`/bff/me` with DTO shape check for `user`, `tenant`, `capabilities`)
- strategy, persona, capital-pools, rebalances, deployments
- evolution-programs, jobs
- approvals, v5/interventions
- alerts, incidents, audit, artifacts, runtimes
- mcp-servers, mcp-tools, skills, channels, tools, ranking-formulas, research-experiments
- agora/signals, agora/inbox, agora/journal, agora/postmortems, agora/ask/sessions
- v5/loop-runs, v5/sentinel/findings, v5/execution/persona-health

### Write-Flow Review

Confirm-token CRUD cycle (`create → read → redeem → delete → read-deleted`) completes cleanly:
- `POST /bff/confirm-tokens` → 201 with `data.tokenId`, `data.status`, `meta.idempotency`
- `GET` → 200, `POST /redeem` → 202, `DELETE` → 202, `GET` → 200
- Receipt envelope contains `meta.durable`, `meta.idempotency`, `meta.liveCapitalSideEffects`

### Auth Handling

- Bearer token is not written to evidence
- Only a 12-character SHA256 fragment of the JWT secret is recorded (`secret_sha256_12`)
- Probe script correctly implements redaction

### Infrastructure Additions

- `scripts/probe_bff_authenticated_live.py` — dependency-light probe, no token leakage
- `services/control-plane/bff/test_bff_oidc_staging_env_contract.py` — verifies dev compose and prod env template forward all BFF auth env variables with correct strict defaults
- Dev compose `docker-compose.yml` updated to forward `PANTHEON_BFF_JWT_SECRET`, `PANTHEON_BFF_JWT_ISSUER`, `PANTHEON_BFF_JWT_AUDIENCE`, `PANTHEON_BFF_DEFAULT_ROLE`, and OIDC variables

## Gate Outcomes

- `VITE_BFF_MODE=live`: **allowed** by this authenticated DTO/write smoke
- `VITE_BFF_REAL_WRITES=true`: no longer blocked by this task for reviewed non-capital safe-write surfaces; final gate depends on downstream FE-005/FE-006 cutover/deploy evidence

## Review Notes (ZH)

審查通過。37/37 probes 全部通過，包含 30 個 read DTO probes 與 5 個 confirm-token write-flow probes，無 live-capital side effects。Token 安全處理符合要求，evidence 僅記錄 secret hash fragment，不含明文 token。基礎設施補齊（probe script、OIDC contract test、dev compose auth forwarding）。Gate outcome 已明確記錄於 task artifact，可供 FE-005/FE-006 downstream 使用。
