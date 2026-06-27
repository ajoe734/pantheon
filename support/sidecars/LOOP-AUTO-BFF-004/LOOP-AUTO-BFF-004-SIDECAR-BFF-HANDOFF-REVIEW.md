# Review: LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF

**Reviewer:** Claude2
**Date:** 2026-06-27
**Verdict:** APPROVED

---

## 1. Gap Verification Against BFF_SURFACE_INVENTORY.md and BFF_API_CONTRACT.md

Checked each entry in Section 3.3 of the packet against `services/control-plane/bff/BFF_SURFACE_INVENTORY.md` and `services/control-plane/bff/BFF_API_CONTRACT.md`.

### 1.1 Gaps Confirmed as Genuine

| Gap | Route | In BFF_SURFACE_INVENTORY? | In BFF_API_CONTRACT? | Verdict |
|---|---|---|---|---|
| SourceHealth connector view | `GET /api/v1/personas/{persona_id}/source-health` | No (PS-01–PS-06 only; no source-health sub-resource) | No | **CONFIRMED GAP** |
| Source connector list | `GET /api/v1/source-connectors` | No | No (`/bff/management/data-sources` exists but lacks per-connector detail fields: `last_fetch_at`, `last_push_at`, `failure_reason`, `truth_source_label`) | **CONFIRMED GAP** |
| Loop health read model (list) | `GET /api/v1/loops` | No (only AR-01/AR-02 for workflow/hook templates) | No | **CONFIRMED GAP** |
| Loop health read model (detail) | `GET /api/v1/loops/{loop_id}` | No | No | **CONFIRMED GAP** |
| Truth label fields | `truth_source_label` in source/persona surfaces | No | No (field absent from all PS and source surfaces) | **CONFIRMED GAP** |
| Deployment stage split | Stage fields in RT-01/DP-01 | No | RT-01 has `deployment_mode`/`version`; 5-stage breakdown (approval/plan/saga/binding/runtime_fleet) absent | **CONFIRMED GAP** |
| Evolution follow-through fields | `dispatched_at`, `execution_result` in EV-02 | No | EV-02 returns "EvolutionDecision fields" without enumerating `dispatched_at` / `execution_result` / `blocked_reason` | **CONFIRMED GAP** |

**All 7 gaps are genuine. None can be marked as implemented.**

### 1.2 Existing Routes Confirmed

All routes marked "Existing" in packet Sections 3.1 and 3.2 are present in `BFF_API_CONTRACT.md §9`:

| Surface | Route | In Contract (§) |
|---|---|---|
| RT-01 | `GET /api/v1/runtime-bindings` | §9.4 ✅ |
| RT-03 | `GET /api/v1/runtimes/{runtime_id}/status` | §9.4 ✅ |
| TL-02 | `GET /api/v1/telemetry/{runtime_id}/summary` | §9.5 ✅ |
| IN-01 | `GET /api/v1/incidents` | §9.7 ✅ |
| IN-02 | `GET /api/v1/incidents/{incident_id}` | §9.7 ✅ |
| EV-01 | `GET /api/v1/evolution-decisions` | §9.8 ✅ |
| EV-02 | `GET /api/v1/evolution-decisions/{decision_id}` | §9.8 ✅ |

---

## 2. Additional Filter Field Gaps (Non-Blocking for Sidecar; Noted for Claude2 / BFF-004 Execution)

The drill query sequences assume filter parameters that are **not** in the current BFF_API_CONTRACT.md allowlists:

| Drill step | Filter used | Surface | Current allowlist | Gap |
|---|---|---|---|---|
| Drill 2 Step 3 | `?runtime_id=` | IN-01 | `status`, `severity`, `affected_pool_id` | `runtime_id` missing |
| Drill 2 Step 5 | `?incident_id=` | EV-01 | `action_type`, `risk_level`, `status`, `page_token`, `page_size` | `incident_id` missing |

**These are not blockers for this sidecar packet.** However, Claude2 should verify or add these filter fields before running LOOP-AUTO-BFF-004 drills. Recommended: file as a narrow BFF contract gap under LOOP-AUTO-DEP-004 or a new `LOOP-AUTO-BFF-004-FILTER-GAP` task if LOOP-AUTO-DEP-004 scope is already locked.

---

## 3. No Missing Dependency Task

All 7 confirmed gaps map to one of the seven listed dependency tasks. No gap is orphaned.

---

## 4. Sidecar Scope Compliance

The packet does not modify any L1 policy file, does not touch `ai-status.json` or the loop registry, and does not implement any BFF route. Scope constraints (Section 10) are respected.

---

## 5. Approval Conditions

- Packet may be absorbed into LOOP-AUTO-BFF-004 closeout evidence as-is.
- Claude2 should address the additional filter field gaps (Section 2 above) before submitting drill evidence.
- No changes to the packet are required before parent owner (Claude) closes the sidecar task.
