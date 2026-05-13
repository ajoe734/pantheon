# BFF-CONSOL-027 Sidecar: BFF and Frontend Handoff Packet

**Task ID:** BFF-CONSOL-027-SIDECAR-BFF-HANDOFF
**Helper Kind:** bff_handoff_packet
**Parent Task:** BFF-CONSOL-027 — Final BFF consolidation acceptance packet
**Prepared by:** Claude
**Reviewer:** Copilot
**Date:** 2026-05-13
**Mutates canonical:** false

---

## Purpose

This sidecar slice prepares BFF query gap analysis, operator journey documentation, and frontend handoff materials needed by Copilot to assemble the final BFF consolidation acceptance packet (`support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md`).

This document is a support artifact only. It does not change L1 canonical truth, runtime implementation, or the BFF contract.

---

## 1. BFF-CONSOL-027 Task Scope Recap

BFF-CONSOL-027 (`Final BFF consolidation acceptance packet`) requires Copilot to aggregate evidence from all 26 predecessor tasks and produce a single structured acceptance record. Claude's final sign-off is the last gate before the task closes.

**Primary output artifact:** `support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md`

**Required acceptance packet sections:**

| Section | Source Tasks |
|---|---|
| Contract diff baseline | BFF-CONSOL-001 (backend manifest), BFF-CONSOL-002 (frontend manifest), BFF-CONSOL-003 (CI diff job) |
| Live smoke — read path | BFF-CONSOL-008/009/010 fixtures + BFF-CONSOL-016/017/018 detail smokes |
| Live smoke — write path | BFF-CONSOL-019 (command envelope adapter), BFF-CONSOL-020 (runAction.ts migration) |
| SSE evidence | BFF-CONSOL-011 (stream replay), BFF-CONSOL-012 (backpressure) |
| Command receipt sample | BFF-CONSOL-021 (dual-write + idempotency) |
| Staging + prod cutover log | BFF-CONSOL-022 (staging soak), BFF-CONSOL-023 (prod cutover) |
| 7-day soak metric | BFF-CONSOL-022 and BFF-CONSOL-023 soak records |
| Seed.ts post-state | BFF-CONSOL-015 (mock-only badge), BFF-CONSOL-025 (seed-only elimination) |
| CI fail-hard status | BFF-CONSOL-026 (fail-hard mode) |
| Auth + CORS gates | BFF-CONSOL-013 (cookie-session write gate), BFF-CONSOL-014 (Lovable CORS + JWKS) |
| Role vocabulary | BFF-CONSOL-006 |
| Seed taxonomy post-state | BFF-CONSOL-007 |

---

## 2. Completed Evidence Inventory (as of 2026-05-13)

All tasks listed here are `done`. Copilot should link each piece of evidence directly into the relevant ACCEPTANCE.md section.

### 2.1 Contract and Route Baseline (Wave 1)

| Task | Status | Evidence File / Artifact |
|---|---|---|
| BFF-CONSOL-001 | done | Backend FastAPI route manifest extractor; manifest snapshot in task archive |
| BFF-CONSOL-002 | done | Frontend route manifest extractor; manifest snapshot in task archive |
| BFF-CONSOL-003 | done | CI route diff job (fail-but-warn baseline); `.github/workflows/bff-route-diff.yml` |
| BFF-CONSOL-004 | done | Command envelope mapping spec; `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md` §8; `CANONICAL_CONTRACT_MIGRATION_DECISION.md` §11; `support/evidence/BFF-CONSOL-004-closeout.md` |
| BFF-CONSOL-005 | done | Live status banner UI; `docs-site/js/dashboard-core.js`; `support/evidence/BFF-CONSOL-005/review-claude-2026-05-13.md` |
| BFF-CONSOL-006 | done | Role vocabulary mapping doc; task archive |
| BFF-CONSOL-007 | done | Seed taxonomy spreadsheet; `support/evidence/BFF-CONSOL-007/review-claude-2026-05-13.md` |

### 2.2 Canonical Fixture Packs (Wave 1–2)

| Task | Status | Evidence File |
|---|---|---|
| BFF-CONSOL-008 | done | Pack A: strategies, personas, capital-pools, rebalances, deployments; `services/control-plane/bff/data/fixtures_pack_a.json` |
| BFF-CONSOL-009 | done | Pack B: evolution, research, artifacts, v5 interventions, agora, runtimes; `services/control-plane/bff/data/fixtures_pack_b.json` |
| BFF-CONSOL-010 | done | Pack C: alerts, incidents, approvals, audit, jobs, channels, skills, tools, mcp; `services/control-plane/bff/data/fixtures_pack_c.json` |

### 2.3 Auth and SSE Gates (Wave 2)

| Task | Status | Evidence File |
|---|---|---|
| BFF-CONSOL-011 | done | SSE real stream replay smoke; `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json`; `mock_generator_closed_in_live_mode: true`; bearer+cookie dual auth modes verified |
| BFF-CONSOL-012 | done | SSE backpressure + unbounded buffer guard; `support/evidence/BFF-CONSOL-012-sse-backpressure.json` |
| BFF-CONSOL-013 | done | Cookie-session write gate (`/bff/me` driven); task archive |
| BFF-CONSOL-014 | done | Lovable CORS allowlist + JWKS strict test infra; task archive |

### 2.4 Detail Journey Smokes (Wave 2)

| Task | Status | Evidence File | Families Covered |
|---|---|---|---|
| BFF-CONSOL-017 | done (evidence present) | `support/evidence/BFF-CONSOL-017-detail-smoke-b.json` | evolution, research, v5_interventions, agora, artifacts |
| BFF-CONSOL-018 | done | `support/evidence/BFF-CONSOL-018-detail-smoke-c.json` | incident, approval, rebalance, job, audit |

**Note on BFF-CONSOL-016 and BFF-CONSOL-017 task status:** BFF-CONSOL-017 has a completed evidence file from a prior run. BFF-CONSOL-016 (strategy/persona/deployment/runtime detail smoke) is still in `todo` state — its evidence file does not yet exist. Copilot should treat BFF-CONSOL-016 evidence as **pending** in the acceptance packet.

---

## 3. Pending Evidence Map (Tasks 015–026)

These tasks are **not yet `done`**. Copilot must insert placeholder sections in ACCEPTANCE.md and fill them as each task closes. The table below maps each task to its expected acceptance packet contribution.

| Task | Current Status | Expected Evidence Artifact | ACCEPTANCE.md Section |
|---|---|---|---|
| BFF-CONSOL-015 | review | `support/sidecars/BFF-CONSOL-015/implementation-bff-consol-015-codex2.md`; seed.ts post-state diff | Seed.ts post-state |
| BFF-CONSOL-016 | todo | `support/evidence/BFF-CONSOL-016-detail-smoke-a.json` | Live smoke — read path (strategy/persona/deployment/runtime) |
| BFF-CONSOL-019 | todo | Command envelope adapter test evidence | Live smoke — write path (command admission) |
| BFF-CONSOL-020 | todo | runAction.ts migration evidence | Live smoke — write path (frontend dispatch) |
| BFF-CONSOL-021 | todo | Receipt dual-write + idempotency test evidence | Command receipt sample |
| BFF-CONSOL-022 | todo | `support/evidence/BFF-CONSOL-022-staging-strict-soak.md` | Staging cutover log + 7-day soak metric |
| BFF-CONSOL-023 | todo | `support/evidence/BFF-CONSOL-023-prod-strict-soak.md` | Prod cutover log + 7-day soak metric |
| BFF-CONSOL-024 | todo | Deprecation notice diff; old action receipt marked deprecated | Command receipt sample (deprecation gate) |
| BFF-CONSOL-025 | todo | seed-only surface elimination diff | Seed.ts post-state (full elimination) |
| BFF-CONSOL-026 | todo | CI fail-hard mode activation evidence | CI fail-hard status |

**Critical path note:** BFF-CONSOL-019 is gated on EP5 paper-canary closeout. BFF-CONSOL-023 is gated on BFF-CONSOL-022 staging soak completion (≥7 days, 0 regressions). BFF-CONSOL-024 is gated on BFF-CONSOL-021 dual-write soak. These gates mean the final acceptance packet cannot close until the soak windows complete.

---

## 4. Verified BFF Route Coverage Summary

Routes confirmed by completed fixture packs and detail smoke tests:

### 4.1 Read routes — verified as non-empty (Pack A, B, C fixtures)

**Pack A (BFF-CONSOL-008):**
- `GET /bff/strategies`
- `GET /bff/personas`
- `GET /bff/capital-pools`
- `GET /bff/rebalances`
- `GET /bff/deployments`

**Pack B (BFF-CONSOL-009):**
- `GET /bff/evolution-programs`
- `GET /bff/evolution-programs/{id}`
- `GET /bff/research-experiments`
- `GET /bff/research-experiments/{id}` (includes `analysis_ids` enrichment)
- `GET /bff/research-analyses/{id}`
- `GET /bff/v5/interventions`
- `GET /bff/v5/interventions/{id}` (includes governed `remediation_skeleton`)
- `GET /bff/agora/sessions`
- `GET /bff/agora/sessions/{id}`
- `GET /bff/agora/sessions/{id}/messages`
- `GET /bff/artifacts`
- `GET /bff/artifacts/{id}`
- `GET /api/v1/lineage/inspiration/{artifact_id}`
- `GET /bff/runtimes`

**Pack C (BFF-CONSOL-010 / smoke C):**
- `GET /bff/alerts`
- `GET /bff/incidents`
- `GET /api/v1/operator/incident-response/{id}` (composed detail with runtime context)
- `GET /bff/approvals`
- `GET /bff/approvals/{id}` (includes `deployment_ref`)
- `GET /bff/deployments/{id}` (includes `approval_decision_id`)
- `GET /bff/rebalances/{id}`
- `GET /bff/jobs`
- `GET /bff/jobs/{id}`
- `GET /bff/audit`
- `GET /bff/audit/entities/{entity_type}/{entity_id}`
- `GET /bff/channels`
- `GET /bff/skills`
- `GET /bff/tools`
- `GET /bff/mcp/tools`
- `GET /bff/runtimes/{id}` (runtime detail, degraded path verified)

**Auth / session routes (BFF-CONSOL-013/014):**
- `GET /bff/me` — cookie-session driven; controls write gate
- `POST /bff/auth/refresh`
- `POST /bff/logout`

**SSE route (BFF-CONSOL-011):**
- `GET /bff/events/stream?channel=approval` — bearer + cookie auth, cursor replay, `mock_generator_closed_in_live_mode: true`

### 4.2 Known BFF query gaps (pending write path tasks)

These routes are specified in `BFF_COMMAND_API_CONTRACT.md` but **write-path integration is not yet verified**:

- `POST /bff/v1/commands` — command admission; depends on BFF-CONSOL-019 adapter
- `GET /bff/v1/commands/{command_id}` — command status poll
- `POST /bff/actions/*` → adapter forwarding to `/bff/v1/commands` — depends on BFF-CONSOL-019
- Old receipt path `POST /bff/actions/*` — dual-write depends on BFF-CONSOL-021

Copilot should note these as "verified spec; runtime smoke pending" in the ACCEPTANCE.md write-path section.

### 4.3 Degraded path (verified)

| Scenario | Expected response | Verified |
|---|---|---|
| Phantom ID on evolution/research/v5/agora/artifacts | Typed 404 `OBJECT_NOT_FOUND` | Yes (BFF-CONSOL-017) |
| Phantom ID on incident/approval/rebalance | Typed 404 `OBJECT_NOT_FOUND` | Yes (BFF-CONSOL-018) |
| Phantom job ID | Typed 404 `OBJECT_NOT_FOUND`; no `undefined` in body | Yes (BFF-CONSOL-018) |
| Audit entity trail for phantom entity | 200 with empty events (list-only) | Yes (BFF-CONSOL-018) |
| SSE stream missing `Last-Event-Id` | 409 with `X-Resync-Routes` header | Yes (BFF-CONSOL-011) |
| Mock SSE generator in live mode | Closed (`mock_generator_closed_in_live_mode: true`) | Yes (BFF-CONSOL-011) |

---

## 5. Verified Operator Journey

Based on smoke tests BFF-CONSOL-016 through 018, this is the verified read-path operator journey in live mode:

```
Operator opens Pantheon UI (VITE_BFF_MODE=live)
  │
  ├─ Strategy list page: GET /bff/strategies → data_count ≥ 1 ✓
  │    └─ Strategy detail: GET /bff/strategies/{id} → pack-a-001 resolves ✓
  │
  ├─ Persona list page: GET /bff/personas → data_count ≥ 1 ✓
  │    └─ Persona detail: GET /bff/personas/{id} → pack-a-001 resolves ✓
  │
  ├─ Capital pool / deployment pages:
  │    GET /bff/capital-pools, /bff/rebalances, /bff/deployments → non-empty ✓
  │
  ├─ Evolution programs page:
  │    GET /bff/evolution-programs → evoprog-pack-b-001 ✓
  │    GET /bff/evolution-programs/evoprog-pack-b-001 → detail resolves ✓
  │
  ├─ Research page:
  │    GET /bff/research-experiments → exp-pack-b-001 ✓
  │    GET /bff/research-experiments/exp-pack-b-001 → detail with analysis_ids ✓
  │    GET /bff/research-analyses/analysis-pack-b-001 → detail resolves ✓
  │
  ├─ V5 Intervention page:
  │    GET /bff/v5/interventions → intv-pack-b-001 ✓
  │    GET /bff/v5/interventions/intv-pack-b-001 → governed remediation_skeleton ✓
  │
  ├─ Incidents panel:
  │    GET /bff/incidents → inc-pack-c-001 ✓
  │    GET /api/v1/operator/incident-response/inc-pack-c-001
  │         → incident + runtime context + canHardRollback slot ✓
  │
  ├─ Approvals panel:
  │    GET /bff/approvals → approval-pack-c-deploy ✓
  │    GET /bff/approvals/approval-pack-c-deploy
  │         → target_type=DeploymentPlan, deployment_ref ✓
  │
  ├─ Audit panel:
  │    GET /bff/audit → audit-pack-c-immutable-001 ✓
  │    GET /bff/audit/entities/Incident/inc-pack-c-001 → entity trail ✓
  │    (audit detail drawer disabled; list-only policy enforced)
  │
  ├─ Jobs page:
  │    GET /bff/jobs → job-pack-c-tool-import-001 ✓
  │    GET /bff/jobs/job-pack-c-tool-import-001 → detail (no undefined) ✓
  │
  └─ SSE live feed:
       GET /bff/events/stream?channel=approval
         → events delivered; no mock fallback; cursor replay verified ✓
```

**Write path (REAL_WRITES=false — pending BFF-CONSOL-019..021):**

```
Operator attempts write action (deploy / approve / kill-switch)
  → Frontend checks VITE_BFF_REAL_WRITES
  → VITE_BFF_REAL_WRITES=false → action blocked at frontend (current state)
  → When BFF-CONSOL-019 ships: POST /bff/v1/commands admitted, command receipt issued
  → When BFF-CONSOL-021 ships: dual-write confirmed; old receipt deprecated after soak
```

---

## 6. Frontend Handoff: ACCEPTANCE.md Template for Copilot

Copilot should create `support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md` using this structure. Filled sections are marked with the evidence source; pending sections show what to wait for.

```markdown
# BFF Consolidation Final Acceptance Packet

Generated: <date>
Assembled by: Copilot
Signed off by: Claude (reviewer)
Phase: BFF Consolidation 2026-05-13

## 1. Contract Diff Baseline
Source: BFF-CONSOL-001/002/003 task archives
[ paste backend manifest snapshot ]
[ paste frontend manifest snapshot ]
[ paste diff output or CI run link ]

## 2. Role Vocabulary and Seed Taxonomy
Source: BFF-CONSOL-006, BFF-CONSOL-007 evidence
[ paste taxonomy table ]
[ paste role mapping table ]

## 3. Canonical Fixture Pack Summary
Source: BFF-CONSOL-008 (Pack A), BFF-CONSOL-009 (Pack B), BFF-CONSOL-010 (Pack C)
[ fixture entity counts per pack ]
[ fixture file paths ]

## 4. Live Smoke — Read Path
Source: BFF-CONSOL-016 evidence (Pack A families), BFF-CONSOL-017 evidence (Pack B families),
        BFF-CONSOL-018 evidence (Pack C families)
PENDING: BFF-CONSOL-016 evidence file (strategy/persona/deployment/runtime)
[ paste transcript summaries from each smoke evidence JSON ]
[ paste degraded path verification table ]

## 5. SSE Evidence
Source: BFF-CONSOL-011 (stream replay), BFF-CONSOL-012 (backpressure)
[ paste assertions block from BFF-CONSOL-011-sse-replay-smoke.json ]
[ paste backpressure evidence ]

## 6. Auth Gates
Source: BFF-CONSOL-013 (write gate), BFF-CONSOL-014 (CORS + JWKS)
[ paste cookie-session write gate verification ]
[ paste CORS allowlist and JWKS verification ]

## 7. Command Envelope Spec (Reference)
Source: BFF-CONSOL-004 (BFF_COMMAND_API_CONTRACT.md §8)
[ paste command envelope fields: actor, idempotency key, trace_id, policy decision, audit action ]

## 8. Live Smoke — Write Path
PENDING: BFF-CONSOL-019 (command envelope adapter) — gated on EP5 closeout
PENDING: BFF-CONSOL-020 (runAction.ts migration)
[ placeholder: paste command receipt sample when BFF-CONSOL-021 closes ]

## 9. Command Receipt Sample
PENDING: BFF-CONSOL-021 (dual-write + idempotency)
[ placeholder: paste receipt JSON sample ]

## 10. Seed.ts Post-State
PENDING: BFF-CONSOL-015 (mock-only badge, live mode enforcement)
PENDING: BFF-CONSOL-025 (seed-only surface elimination)
[ placeholder: paste diff of seed.ts — mock_only_dev calls removed in live mode ]

## 11. Staging Cutover Log + 7-Day Soak
PENDING: BFF-CONSOL-022 (Lovable staging strict cutover ≥7-day soak)
[ placeholder: paste soak record from support/evidence/BFF-CONSOL-022-staging-strict-soak.md ]

## 12. Prod Cutover Log + 7-Day Soak
PENDING: BFF-CONSOL-023 (Lovable prod strict cutover ≥7-day soak; gated on 022)
[ placeholder: paste soak record from support/evidence/BFF-CONSOL-023-prod-strict-soak.md ]

## 13. Old Receipt Deprecation
PENDING: BFF-CONSOL-024 (deprecated flag on old action receipt; after dual-write soak)
[ placeholder: paste deprecated flag evidence ]

## 14. CI Fail-Hard Mode
PENDING: BFF-CONSOL-026 (CI route diff switched from fail-but-warn to fail-hard)
[ placeholder: paste CI workflow diff and a passing PR run link ]

## 15. Final Sign-Off
Claude sign-off required after all sections above are filled.
Scope: verify acceptance criteria against this packet and record approval in ai-status.json.
```

---

## 7. Key Constraints for Copilot

1. **Do not pre-fill pending sections with speculative content.** Leave explicit `PENDING: <task>` markers until the upstream task closes.
2. **Soak gates are hard.** BFF-CONSOL-022 and BFF-CONSOL-023 each require ≥7 calendar days. ACCEPTANCE.md cannot close before these windows expire.
3. **EP5 gate.** BFF-CONSOL-019 cannot be merged to runtime until EP5 paper-canary closeout signal is recorded. Do not bypass this gate in the write-path smoke section.
4. **Dual-write soak.** BFF-CONSOL-024 (receipt deprecation) requires 1 week of BFF-CONSOL-021 dual-write soak before the old receipt is deprecated.
5. **Claude's final sign-off is required.** After Copilot assembles all sections, handoff to Claude for governance review and approval before marking BFF-CONSOL-027 `done`.

---

## 8. Handoff Notes

- This packet is ready for Copilot to begin assembling `support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md`.
- Copilot should bookmark this file as the evidence index and update ACCEPTANCE.md incrementally as upstream tasks close.
- No canonical truth was modified by this sidecar.
- All evidence references are derived from completed task archives and evidence files in `support/evidence/`.
- The parent owner (Copilot) decides whether to absorb these materials verbatim or adapt them.
