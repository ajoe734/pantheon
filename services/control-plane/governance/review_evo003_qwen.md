# EVO-003 Review — Qwen

**Reviewer:** Qwen  
**Owner:** Codex  
**Date:** 2026-04-10  
**Status:** ✅ APPROVED

---

## 1. Scope of Review

Reviewed the following artifacts:

- `services/control-plane/governance/evolution_decision.py` (1102 lines)
- `services/control-plane/governance/evolution_decision.schema.json`
- `services/control-plane/governance/evolution_decision.contract.md`
- `services/control-plane/governance/test_evolution_decision.py` (17 tests)
- `services/control-plane/governance/smoke_test_evolution_decision.py` (16 checks)
- `services/control-plane/governance/review_evo003_codex_zh.md` (owner's review packet)
- Cross-references: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `services/control-plane/governance/contract.md`, `services/incident/contract.md`, `services/registry/lineage/read_model_contract.md`

Verified:
- `python3 -m unittest services/control-plane/governance/test_evolution_decision.py` → **17 PASS**
- `python3 services/control-plane/governance/smoke_test_evolution_decision.py` → **16 PASS**

---

## 2. Acceptance Criteria Check

| Criterion | Status | Notes |
|---|---|---|
| decision lifecycle formalized | ✅ PASS | `proposed → reviewed → approved → executed|rejected|canceled → superseded` enforced via state machine with transition guards |
| actor roles formalized | ✅ PASS | Review/approval/execution matrices match both L1 policy docs exactly |
| evidence links formalized | ✅ PASS | `EvidenceRef[]`, `ThresholdSnapshot[]`, `linked_postmortem_id`, `linked_incident_id` — at least one required |
| cooldown and observation fields formalized | ✅ PASS | All four timestamp fields required on `executed`; validated for ordering |
| single-active-rule enforced | ✅ PASS | `EvolutionDecisionStore._enforce_single_active_rule()` blocks parallel active decisions on same target |
| reverse-link into Postmortem wired | ✅ PASS | `IncidentStore.link_evolution_decision()` called on put when `linked_postmortem_id` is present |

---

## 3. Detailed Findings

### 3.1 Risk Level Normalization — ✅ CORRECT

Checked against both L1 policies:

| Action | L1 Policy | Code (`infer_risk_level`) | Match |
|---|---|---|---|
| `retrain` | low (§5.1) | `LOW_RISK_ACTIONS` | ✅ |
| `freeze` + `paper` | medium (§5.2 `freeze_paper`) | returns `MEDIUM` | ✅ |
| `freeze` + `canary` | medium (§5.2 `freeze_canary`) | returns `MEDIUM` | ✅ |
| `freeze` + `live` | high (§5.3 `freeze_live_strategy`) | returns `HIGH` | ✅ |
| `retire` | high (§5.3) | `HIGH_RISK_ACTIONS` | ✅ |
| `split_persona` / `merge_persona` | high (§5.3) | `HIGH_RISK_ACTIONS` | ✅ |
| `freeze` without `target_stage` | — | raises `EvolutionDecisionError` | ✅ |

The contract.md (§7.5 EVO risk mapping example) correctly states `retrain = low`, fixing the previously noted drift where `retrain` could have been misread as medium.

### 3.2 Single-Active-Rule on Executed-but-Still-Observing — ✅ CORRECT

`is_active()` returns `True` for:
- `proposed`, `reviewed`, `approved` (always active)
- `executed` with unfinished cooldown/observation window (`as_of <= max(cooldown_ends_at, observation_window_ends_at)`)

`_enforce_single_active_rule()` only blocks when both candidate and existing are active. Once `superseded` or observation expires, new decisions are allowed. This matches `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.1–§5.3 exactly.

### 3.3 Reverse-Link Sync — ✅ SUFFICIENT

When `EvolutionDecisionStore.put()` sees `linked_postmortem_id` and an `incident_store` is injected, it calls `IncidentStore.link_evolution_decision(postmortem_id, decision_id)`. The incident contract (`services/incident/contract.md` §3, §146, §193) documents this as the canonical reverse-link mechanism setting `Postmortem.linked_evolution_decision_id`. The test `test_postmortem_reverse_link_is_synced` verifies end-to-end behavior.

This is sufficient as the incident-side integration. The reverse direction (reading all evolution decisions linked to a postmortem) is covered by `find_by_postmortem()`.

### 3.4 Action Normalization — ✅ CLEAN

The normalized `action_type` enum (`freeze`, `retire`, etc.) plus `target_stage`/`target_type` separation is well-designed. It allows BFF EV-01/EV-02 list/detail filters to use single fields while preserving stage/type semantics. The mapping table in `evolution_decision.contract.md` §4 is clear and complete.

### 3.5 ApprovalDecision Integration — ✅ CORRECT

- `approval_decision_id` required from `reviewed` onward (enforced in validation)
- Schema `allOf` conditional requires `approval_decision_id` for `reviewed`, `approved`, `rejected`, `executed` states
- The `ApprovalDecision` contract (§7) documents `target_type = "evolution_proposal"` for evolution proposals

### 3.6 Schema Validation — ✅ COMPLETE

The JSON schema correctly uses:
- `allOf` conditionals for state-dependent required fields
- `if/then` for `freeze` → `target_stage` requirement
- Proper enum constraints on all categorical fields
- `format: date-time` on all timestamp fields

---

## 4. Minor Observations (Non-Blocking)

1. **`ReviewStep.from_dict`** does not validate `step_type` or `actor_role` against enums during deserialization — it stores raw strings. The `validate()` method catches invalid values later, which is acceptable for a dataclass parse pattern, but worth noting for future API-layer consumers.

2. **`cancel` method** allows cancel from `approved` state but does not require an `ApprovalDecision` update to reflect the cancellation. This is a downstream integration concern (the ApprovalDecision caller is responsible for updating), not a contract bug.

3. **No `revive` risk entry in L1 §5** — `revive` appears in the code as `HIGH_RISK_ACTIONS` but the L1 policy document doesn't explicitly list it in §5.3. This is a minor policy doc gap; the code choice (high risk for reviving a retired strategy) is defensible.

---

## 5. Verdict

**APPROVED.** EVO-003 delivers a first-class `EvolutionDecision` contract that:

- Formalizes the full lifecycle with state-transition guards
- Implements correct review/approval/execution role matrices per risk level
- Enforces the single-active-rule invariant at the store layer
- Integrates bidirectionally with the incident/postmortem backbone
- Normalizes action types cleanly for downstream BFF consumption
- Passes all 17 unit tests and 16 smoke tests

All acceptance criteria are met. The contract is ready for downstream consumers (EVO-004, EV-01/EV-02, loop policy).
