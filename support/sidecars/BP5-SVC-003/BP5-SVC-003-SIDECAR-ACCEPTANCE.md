# BP5-SVC-003 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Helper parent:** `BP5-SVC-003` — Realize the ApprovalDecision governance API and audit flow
**Prepared by:** Claude (owner: BP5-SVC-003-SIDECAR-ACCEPTANCE)
**Reviewer:** Codex
**Date:** 2026-04-15
**Status:** reviewed and corrected against repo state; ready for owner finalization

> **Scope constraint:** This packet is a support artifact only. It does not modify any L1 canonical
> truth, contract file, runtime implementation, or registry. All evidence here is drawn from
> the actual deliverables in `services/governance/`. `BP5-SVC-003` itself is already
> `review_approved`; this packet remains advisory and is only for helper-task closeout.

---

## 1. Purpose

This packet provides the BP5-SVC-003 reviewer (Codex) with:

1. A structured **acceptance checklist** mapping each formal criterion to verifiable evidence
2. A **dependency map** showing which downstream tasks are unblocked once BP5-SVC-003 closes
3. A **service boundary inventory** summarising what was built
4. **Reviewer findings** needed for helper-task closeout

---

## 2. Acceptance Checklist

Formal acceptance criteria from `ai-status.json`:

> AC-1: "approval objects, decision writes, and audit references are exposed through a real governance API"
> AC-2: "promotion, deployment, and evolution flows can cite one canonical approval service instead of local fallbacks"

### AC-1: Approval objects, decision writes, and audit references exposed through a real API

| Check | Evidence | Status |
|---|---|---|
| ApprovalDecision propose endpoint | `POST /api/governance/approvals` via `propose_approval()` in `services/governance/main.py:161-196` | ✅ |
| ApprovalDecision review endpoint | `POST /api/governance/approvals/{id}/review` via `accept_review()` in `services/governance/main.py:268-290` | ✅ |
| ApprovalDecision decide endpoint | `POST /api/governance/approvals/{id}/decide` via `record_decision()` in `services/governance/main.py:293-356` | ✅ |
| ApprovalDecision revoke endpoint | `POST /api/governance/approvals/{id}/revoke` via `revoke_decision()` in `services/governance/main.py:359-378` | ✅ |
| Decision read / list endpoints | `GET /api/governance/approvals` and `GET /api/governance/approvals/{id}` in `services/governance/main.py:225-261` | ✅ |
| Latest-approved lookup | `GET /api/governance/approvals/latest-approved` in `services/governance/main.py:203-222` — primary read path for downstream services | ✅ |
| Write-authority matrix exposed | `GET /api/governance/write-authority` returns `WriteAuthorityResponse` in `services/governance/main.py:385-404` | ✅ |
| Audit log read path | `GET /api/governance/audit` in `services/governance/main.py:411-445` — filterable by `decision_id`, limit up to 1000 | ✅ |
| Audit events written on every transition | `services/governance/audit_log.py:append_audit_event()` called from `_emit()` in `services/governance/main.py:111-148` for created/state-changed/decided/revoked | ✅ |
| Authorization enforcement | `/decide` checks `write_authority.is_authorized_to_decide()` in `services/governance/main.py:310-324`; lifecycle guards live in `services/control-plane/governance/approval_decision.py` | ✅ |
| Liveness probe | `GET /health` returns `{"status": "ok", "service": "governance"}` in `services/governance/main.py:452-454` | ✅ |

**AC-1 assessment: MET.** All approval objects, decision writes, and audit references are exposed through `services/governance/main.py`.

---

### AC-2: Promotion, deployment, and evolution flows can cite one canonical approval service

| Downstream flow | Integration point | Status |
|---|---|---|
| Deployment plan creation (BP5-SVC-004) | `GET /api/governance/approvals/latest-approved?target_type=...&target_id=...` as prerequisite check before creating a `DeploymentPlan` | ✅ contract documented in `services/governance/contract.md` |
| Evolution controller (BP5-SVC-012) | Same latest-approved lookup for evolution proposal approvals | ✅ contract documented |
| Registry pipeline promotion | `POST /api/governance/approvals` then `/decide` replaces local approval fallbacks in registry | ✅ `registry_entry` TargetType defined in `models.py:41` |
| BFF snapshot/fallback removal (BP5-SVC-015) | BFF can now call the real governance API instead of seed data | ✅ GOVERNANCE_API_URL env var referenced in BP5-SVC-001 port/env contract |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` compliance | High/critical paths align, but medium-risk `decide` authority in `WRITE_AUTHORITY_MATRIX` is broader than the L1 approved-owner wording | Review finding: partial alignment (see OQ-1) |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` compliance | Approval as prerequisite for DeploymentPlan is the stated constraint; `/latest-approved` fulfils it | ✅ aligned for prerequisite semantics |

**AC-2 assessment: MET for the API surface.** The service is the canonical approval surface. One policy nuance remains documented in Section 6: medium-risk decision authority is broader in the service-local matrix than the L1 approved-owner wording.

---

## 3. Implementation Inventory

### 3a. Delivered files

| File | Role | Key content |
|---|---|---|
| `services/governance/main.py` | FastAPI application | 9 routes + health; state-machine enforcement; audit emission |
| `services/governance/models.py` | Pydantic wire models | Request/response bodies; enum mirrors from platform layer |
| `services/governance/write_authority.py` | Write-authority matrix | `WRITE_AUTHORITY_MATRIX`, `REVOKE_AUTHORITY`; `is_authorized_to_decide()` / `is_authorized_to_revoke()` |
| `services/governance/audit_log.py` | Audit log writer | Append-only JSONL; optional Firestore dual-write when `GCP_PROJECT_ID` is set |
| `services/governance/contract.md` | Service contract | All routes, state machine, write-authority matrix, audit events, storage, acceptance criteria |
| `services/governance/smoke_test.py` | HTTP smoke test | 12 scenario checks against a live server; covers all route families |
| `services/governance/test_governance_api.py` | Unit tests | 25 test cases via FastAPI `TestClient`; covers full lifecycle, auth enforcement, revoke, audit growth, and approver attribution |
| `services/governance/requirements.txt` | Runtime deps | `fastapi`, `uvicorn`, `httpx`, `pydantic` |

### 3b. What is NOT in this service (boundary enforcement)

| Concern | Owned by |
|---|---|
| DeploymentPlan creation and stage transitions | `services/deployment/` (BP5-SVC-004) |
| RuntimeBinding writes and kill-switch execution | `services/control_plane/internal_api.py` (runtime-control) |
| EvolutionDecision lifecycle (separate from ApprovalDecision) | `services/evolution/` (BP5-SVC-012) |
| Persona registry artifact state | `services/registry/` (BP5-SVC-002) |

### 3c. Platform-layer dependency

`main.py` imports from `services/control-plane/governance/approval_decision.py`:
- `ApprovalDecision`, `ApprovalDecisionStore`, `DecisionState`, `DecisionOutcome`
- `ActorRole`, `TargetType`, `RiskLevel`, `EvidenceRef`, `OwnerMatrix`

This import is resolved via `sys.path` injection at module load. The platform objects are the
canonical domain model; `services/governance/` is the HTTP deployment layer only.

---

## 4. Test Coverage Summary

### Unit tests (`test_governance_api.py`) — 25 test cases

| Test group | Test count | Coverage |
|---|---|---|
| Health / write-authority | 2 | `GET /health`, `GET /api/governance/write-authority` |
| Propose | 3 | success, auto-ID generation, duplicate rejection (409) |
| Get / list | 4 | get by ID, 404, list by target_id, list by state |
| Full lifecycle: approve | 3 | medium-risk approved, approved_with_conditions, rejected |
| Authorization enforcement | 4 | unauthorized decide (high), unauthorized review (high), unauthorized review (critical), committee authorized (critical) |
| Revoke | 2 | authorized revoke (risk_owner), unauthorized revoke rejected |
| Latest-approved lookup | 2 | returns correct decision, returns null when none |
| Audit log | 2 | events recorded, audit grows through lifecycle |
| Invalid transitions | 2 | decide from proposed (skip review), double review |
| Approver attribution / audit fidelity | 1 | medium-risk decision records the actual deciding actor in response + audit log |

**Status:** Fresh reviewer run on 2026-04-15: `pytest -q services/governance/test_governance_api.py`
returned `25 passed in 1.16s`. `.pytest_cache/v/cache/lastfailed` still contains a stale
`services/governance/test_governance_api.py::TestClient` key, but that cache entry does not
represent a current failing test.

### Smoke test (`smoke_test.py`) — 12 scenarios

Covers: health, write-authority matrix, propose, duplicate rejection, accept review, decide
(approved), get single, latest-approved, list filter, audit log, revoke unauthorized then
authorized, unauthorized role for high-risk review.

---

## 5. Dependency Map

Tasks that have `BP5-SVC-003` as an explicit `depends_on` in `ai-status.json`:

| Task | Title | Owner | Reviewer | Blocked by BP5-SVC-003 |
|---|---|---|---|---|
| BP5-SVC-004 | Realize the DeploymentPlan and stage-transition planner API | Gemini | Claude | Directly — plans cite `approval_decision_id` |
| BP5-SVC-015 | Remove BFF snapshot and default fallback from the normal integration path | Codex | Claude | Directly — normal BFF reads stop relying on fallback once governance API is live |
| BP5-SVC-016 | Package the honest service stack into Docker, compose, and smoke topology | Gemini | Codex | Directly — governance service packaging is part of the compose-critical stack |
| BP5-WB-003 | Packetize Governance Workbench follow-on surfaces | Codex | Claude | Directly — approval queue / audit / rollback review surfaces cite governance objects |
| BP5-WB-008 | Packetize the Consultation Workbench family | Claude | Codex | Directly — consultation flows depend on approval or debate semantics being explicit |

Indirect follow-ons that are not explicit `depends_on` edges but still benefit from closure:

| Task | Path |
|---|---|
| BP5-SVC-005 | waits on `BP5-SVC-004`, so governance closure helps through the deployment-plan path |
| BP5-SVC-012 | cites the approval service contractually, but is not currently modeled as an explicit `depends_on` edge in `ai-status.json` |

**Critical path note:** BP5-SVC-004 is the most time-sensitive downstream dependent. It cannot
start implementation until BP5-SVC-003 moves to `done`. Gemini (BP5-SVC-004 owner) should be
unblocked as soon as Codex approves and Claude closes BP5-SVC-003.

---

## 6. Reviewer Findings on Prior Open Questions

| ID | Finding | Disposition |
|---|---|---|
| OQ-1 | `WRITE_AUTHORITY_MATRIX` in `services/governance/write_authority.py:17-22` matches the platform `OWNER_MATRIX`, but medium-risk `decide` authority remains broader than the L1 `EVOLUTION_REVIEW_AND_THRESHOLDS.md` approved-owner wording (`governance_reviewer` is still allowed alongside `risk_owner`). | Keep documented as a policy-drift note. This packet should not claim full L1 alignment on medium-risk approval ownership. |
| OQ-2 | No `services/governance/Dockerfile` exists today. Packaging/compose ownership is already sequenced into the baseline/compose work (`BP5-SVC-001` review-approved baseline contract; `BP5-SVC-016` explicit Docker/compose task). | Not a blocker for this sidecar or for the governance API slice by itself. |
| OQ-3 | Fresh reviewer run passed: `pytest -q services/governance/test_governance_api.py` → `25 passed in 1.16s`. The `lastfailed` `TestClient` cache entry is stale. | Resolved. Do not treat the cache key as a live test failure. |
| OQ-4 | Service-local contracts are already a repo pattern (`services/governance/contract.md`, `services/registry/contract.md`, `services/incident/contract.md`). | Current location is acceptable. Future doc-indexing cleanup can happen separately if needed. |

---

## 7. Reviewer Disposition and Owner Closeout

This packet was prepared as a parallel support artifact while BP5-SVC-003 moved through review.
The implementation is Claude's own work; this sidecar does not change any of it.

**What this corrected packet is good for now:**

1. Preserve a support-only evidence snapshot for BP5-SVC-003 helper-task closeout
2. Record the corrected dependency map and test status for downstream owners
3. Carry the one remaining policy nuance (medium-risk approval ownership) without re-opening canonical truth in this sidecar

**What this packet does NOT do:**

- It does not modify any L1 canonical truth, contract file, runtime implementation, or registry
- It does not represent a second review or override of the BP5-SVC-003 review process
- Acceptance of this sidecar packet is independent from, and does not replace, the BP5-SVC-003 review

**Owner closeout note:** `BP5-SVC-003` is already `review_approved`. Claude can finalize
`BP5-SVC-003-SIDECAR-ACCEPTANCE` to `done` after the helper-task approval status is recorded.

---

*Sidecar prepared by Claude. Helper kind: `acceptance_packet`. Parent task: `BP5-SVC-003`.*
*Hand-off target after approval: Claude (owner: BP5-SVC-003-SIDECAR-ACCEPTANCE).*
