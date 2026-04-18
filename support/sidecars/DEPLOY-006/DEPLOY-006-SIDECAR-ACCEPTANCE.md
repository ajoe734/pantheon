# DEPLOY-006 Acceptance Packet

**Task:** DEPLOY-006-SIDECAR-ACCEPTANCE  
**Parent task:** DEPLOY-006 — Single-VM end-to-end smoke test  
**Owner:** Claude  
**Reviewer:** Codex  
**Generated:** 2026-04-18  
**Finalized:** 2026-04-18 (review_approved → done)  
**Kind:** sidecar / acceptance_packet  
**Mutates canonical:** false

---

## 1. Parent Task Summary

DEPLOY-006 verifies that the full service chain runs correctly on a single VM using `docker-compose.yml`. The deliverables are:

| Artifact | Path |
|---|---|
| Smoke test script | `scripts/smoke_test_single_vm.sh` |
| Smoke results doc | `docs/deployment/single-vm-smoke-results.md` |

Both artifacts are present and complete as of this packet.

---

## 2. Dependency Map

```
DEPLOY-005 (done)
  └── phase6-2026-04-16-oss-ecosystem-closure
        └── DEPLOY-006 (review)
              ├── scripts/smoke_test_single_vm.sh      ← primary executable
              └── docs/deployment/single-vm-smoke-results.md ← acceptance record
```

DEPLOY-006 has no downstream task blockers at the time of this packet. The next planned deployment milestone (DEPLOY-007+) is not yet materialized.

---

## 3. Acceptance Criteria Checklist

All four criteria are sourced directly from the DEPLOY-006 task in `ai-status.json`.

| # | Criterion (zh) | Criterion (en) | Coverage evidence |
|---|---|---|---|
| 1 | 所有核心服務 healthcheck 通過 | All core service healthchecks pass | `health_check` calls for 6 services in smoke script §1; each asserts HTTP 200 and `status=ok` |
| 2 | BFF 能查詢 registry/governance/telemetry 主要路徑 | BFF can query main registry/governance/telemetry paths | Script §2 (governance write-authority matrix), §9 (BFF surfaces: `/api/v1/operator/governance/review-queue`, `/api/v1/deployment-plans`, `/api/v1/incidents`, `/api/v1/telemetry`) |
| 3 | mock DeploymentPlan 建立成功 | Mock DeploymentPlan created successfully | Script §§4–7: registry register → governance propose/review/decide → runtime-manager deploy → telemetry ingest counter increment |
| 4 | smoke test script 可重複執行 | Smoke test script is re-runnable | Unique `RUN_SUFFIX` (8-char uuid hex) per execution; no fixed IDs that would conflict on re-run |

**Overall gate: all 4 criteria are covered by `scripts/smoke_test_single_vm.sh`.**

---

## 4. Service Coverage Map

| Service | Port | Health endpoint | Smoke actions |
|---|---|---|---|
| runtime-manager | 18081 | `/__health__` (service=runtime-manager) | Deploy RuntimeBinding via `/api/runtimes/deploy` |
| governance | 18082 | `/health` (service=governance) | Write-authority matrix; propose → review → decide approval |
| registry | 18087 | `/health` (service=pantheon-registry) | Register artifact; GET by ID |
| telemetry | 18083 | `/__health__` (service=telemetry-ingest) | Stats baseline; ingest event; confirm counter increment |
| incidents | 18090 | `/__health__` | Create incident linked to binding |
| operator-bff | 18001 | `/health` | Query governance review-queue, deployment-plans, incidents, telemetry surfaces |

Note: the registry health endpoint exposed at port 18087 is `/health`, not `/__health__`. The compose internal healthcheck targets `/__health__`, but the external smoke path is `/health` — this is correctly reflected in the script as of the current implementation.

---

## 5. Mock Plan Flow (End-to-End Path)

```
1. Registry:         POST /api/registry/entries          → 201, registry_id confirmed
2. Governance:       POST /api/governance/approvals       → 201, decision_state=proposed
3. Governance:       POST /approvals/{id}/review          → 200, decision_state=under_review
4. Governance:       POST /approvals/{id}/decide          → 200, decision=approved, decision_state=decided
5. Runtime-manager:  POST /api/runtimes/deploy            → 201, binding_id returned, deployment_mode=paper
6. Telemetry:        POST /api/telemetry/ingest           → 202, status=accepted; GET stats confirms counter++
7. Incidents:        POST /api/incidents                  → 201, incident_id confirmed
8. BFF:              GET  /api/v1/operator/governance/review-queue → 200, meta non-empty
                     GET  /api/v1/deployment-plans        → 200
                     GET  /api/v1/incidents               → 200
                     GET  /api/v1/telemetry               → 200, meta non-empty
```

The governance flow uses the three-step `propose → review → decide` path required by the write-authority matrix.  
The runtime-manager receives an authenticated request via Bearer token (`PANTHEON_RUNTIME_MANAGER_TOKEN`).  
The telemetry counter check (`TOTAL_AFTER >= TOTAL_BEFORE + 1`) is the only numerical assertion across the chain.

---

## 6. Known Limitations and Out-of-Scope Items

| Item | Detail |
|---|---|
| BFF deployment-plans list may be empty | The BFF reads plans from a shared-volume data file; the smoke confirms HTTP 200 only, not a non-empty list |
| Incident ↔ telemetry cross-query | Smoke does not verify the incidents service reverse-looks up telemetry events by `binding_id` |
| Dual-VM cross-plane path | Covered separately in `docs/deployment/dual-vm-acceptance-results.md`; not in scope for DEPLOY-006 |
| LEAN runtime order loop | Out of scope until DEPLOY-010+ |
| Real capital allocation / live-stage gating | Not tested; smoke uses `target_stage=paper` throughout |

---

## 7. Pre-run Checklist (for Codex review)

The following must be true before a passing smoke run can be recorded:

- [ ] `docker compose up -d` completes; all services reach `healthy`
- [ ] postgres, minio, nats are healthy before application services
- [ ] runtime-manager, governance, registry, telemetry, incidents are healthy before operator-bff
- [ ] `PANTHEON_RUNTIME_MANAGER_TOKEN` is set (default: `runtime-control-internal`)
- [ ] Script exits 0 and prints `==> Single-VM smoke passed`

---

## 8. Reviewer Handoff Notes

This packet is a support artifact only. It does not modify:
- `ai-status.json`
- Any L1 canonical policy document
- The smoke script or results doc themselves

**Codex review focus:**
1. Do all four acceptance criteria have adequate script coverage? (see §3)
2. Is the service coverage map consistent with the script implementation? (see §4)
3. Are the known limitations accurate and complete? (see §6)
4. Is the mock plan flow order correct per the write-authority matrix semantics? (see §5)

If this packet is satisfactory, approve via `ai-status.sh approve DEPLOY-006-SIDECAR-ACCEPTANCE` and return to Claude for finalization.
