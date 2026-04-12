# INC-001 Acceptance Packet (Sidecar)

**Parent Task**: `INC-001` — Define incident and postmortem backbone objects
**Parent Owner**: Claude
**Parent Reviewer**: Codex
**Parent Status**: `review_approved`
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-10T14:00:00Z
**Reviewed**: 2026-04-10T14:09:00Z

> This is a **support artifact** only. It does not modify canonical truth, L1 policy documents, or core runtime implementations. It is a readiness packet for the parent owner to use when transitioning INC-001 to `done`.

---

## 1. Dependency Map

### Upstream Dependencies (must be satisfied before INC-001 → done)

| Dependency | Task ID | Status | Relevance to INC-001 |
|---|---|---|---|
| Telemetry capture baseline | `TEL-001` | done | Provides `telemetry_event_ids` field and traceability vocabulary used in `IncidentCase` |
| Lineage read model contract | `LIN-001` | review_approved | Defines normalized edge inventory that `IncidentCase.binding_id` and `Postmortem.incident_id` join against |
| Telemetry ingest shock absorption | `TEL-002` | done | Ensures telemetry events referenced by `IncidentCase.telemetry_event_ids` are durably buffered |

### Downstream Consumers (blocked on INC-001)

| Consumer | Task ID | Status | How it uses INC-001 |
|---|---|---|---|
| EvolutionDecision as first-class governed object | `EVO-003` | in_progress | Links `EvolutionDecision.linked_postmortem_id → Postmortem` |
| Operational evolution boundaries | `EVO-004` | todo | Uses incident severity/status to trigger freeze/rollback/retrain |
| Kill-switch and safe-mode fast path | `EVO-005` | todo | Routes through IncidentCase for audit trail |
| Operator-facing deployment/incident/evolution surfaces | `APP-002` | todo | Surfaces IncidentCase and Postmortem to operators |

### L1 Policy Documents Referenced

| Document | Section Used |
|---|---|
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | §9 Traceability Rules, lineage edge chain, SLA targets |
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | §7.5 Incident/Evolution thresholds, risk-tier routing |
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | freeze vs. rollback distinction |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | RuntimeBinding schema reference |

---

## 2. Acceptance Checklist

The parent task's single acceptance criterion is:

> **incident and postmortem objects attach to stage, binding, and lineage refs**

This packet expands that into a verifiable checklist.

### 2.1 Schema Completeness

| # | Check | Status | Evidence |
|---|---|---|---|
| S1 | `IncidentCase` JSON schema defines all 14 required fields | ✅ PASS | `services/incident/incident_case.schema.json` |
| S2 | `IncidentCase` JSON schema defines all 4 optional fields | ✅ PASS | `services/incident/incident_case.schema.json` |
| S3 | `Postmortem` JSON schema defines all 15 required fields | ✅ PASS | `services/incident/postmortem.schema.json` |
| S4 | `Postmortem` JSON schema defines all 6 optional fields | ✅ PASS | `services/incident/postmortem.schema.json` |
| S5 | Enum constraints defined for severity, status, deployment_stage | ✅ PASS | Both schemas use `enum` arrays |
| S6 | `additionalProperties: false` enforced on both schemas | ✅ PASS | Both schemas set this constraint |
| S7 | Date-time fields use `format: "date-time"` | ✅ PASS | `created_at`, `resolved_at`, `published_at` |

### 2.2 Data Model Integrity

| # | Check | Status | Evidence |
|---|---|---|---|
| D1 | `IncidentCase` Python dataclass has 14 required + 4 optional fields | ✅ PASS | `services/incident/incident.py` |
| D2 | `Postmortem` Python dataclass has 15 required + 6 optional fields | ✅ PASS | `services/incident/incident.py` |
| D3 | `__post_init__` validation on both dataclasses | ✅ PASS | Validates enums for severity, status, deployment_stage |
| D4 | `validate_incident_case()` checks required fields non-empty | ✅ PASS | `services/incident/incident.py` |
| D5 | `validate_postmortem()` checks required fields non-empty | ✅ PASS | `services/incident/incident.py` |
| D6 | `resolved_at` required when status is `resolved` or `closed` | ✅ PASS | Validation logic in `validate_incident_case()` |
| D7 | `published_at` required when postmortem status is `published` | ✅ PASS | Validation logic in `validate_postmortem()` |

### 2.3 Formal Lineage Edges

| # | Check | Status | Evidence |
|---|---|---|---|
| L1 | `IncidentCase.binding_id → RuntimeBinding` edge defined | ✅ PASS | Contract §Formal Edges |
| L2 | `Postmortem.incident_id → IncidentCase` edge defined | ✅ PASS | Contract §Formal Edges |
| L3 | `EvolutionDecision.linked_postmortem_id → Postmortem` edge defined | ✅ PASS | Contract §Formal Edges, EVO-003 linkage |
| L4 | Edge vocabulary matches LIN-001 normalized edge inventory | ✅ PASS | Both use `binding_id` as the join key |

### 2.4 Evidence Propagation

| # | Check | Status | Evidence |
|---|---|---|---|
| E1 | 9 evidence fields propagated from IncidentCase to Postmortem | ✅ PASS | `binding_id`, `deployment_stage`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id` |
| E2 | `IncidentStore.create_postmortem()` enforces exact field match | ✅ PASS | `_validate_postmortem_against_incident()` rejects mismatches |
| E3 | Mismatched evidence causes rejection (not silent drift) | ✅ PASS | Negative test cases in `test_incident.py` |
| E4 | Evidence is a snapshot at incident creation time (not updated if RuntimeBinding changes) | ✅ PASS | Contract states forensic value is the value at creation time |

### 2.5 Referential Integrity

| # | Check | Status | Evidence |
|---|---|---|---|
| R1 | `create_postmortem` requires incident exists | ✅ PASS | `IncidentStore` checks incident_id |
| R2 | `create_postmortem` requires all propagated fields match | ✅ PASS | `_validate_postmortem_against_incident()` |
| R3 | `update_incident_status` validates state transitions | ✅ PASS | Status enum validation |
| R4 | `update_postmortem_status` validates state transitions | ✅ PASS | Status enum validation |
| R5 | `link_evolution_decision` validates both sides exist | ✅ PASS | Store method implementation |

### 2.6 L1 Traceability

| # | Check | Status | Evidence |
|---|---|---|---|
| T1 | `IncidentCase.trace_id` field present | ✅ PASS | Required field in schema and dataclass |
| T2 | `Postmortem.trace_id` field present (propagated) | ✅ PASS | Required field in schema and dataclass |
| T3 | L1 traceability vocabulary from `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` §9 satisfied | ✅ PASS | All 6 required trace fields present: `trace_id`, `runtime_id`, `artifact_id`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id` |

### 2.7 Contract Documentation

| # | Check | Status | Evidence |
|---|---|---|---|
| C1 | `services/incident/contract.md` documents all backbone objects | ✅ PASS | Full contract with fields, edges, and lifecycle |
| C2 | Contract references downstream consumers (EVO-003, EVO-004, APP-002) | ✅ PASS | Contract §Downstream Consumers |
| C3 | Contract documents evidence snapshot semantics | ✅ PASS | Contract §Evidence Propagation |

### 2.8 Test Coverage

| # | Check | Status | Evidence |
|---|---|---|---|
| T1 | Unit tests: ≥36 checks | ✅ PASS | **75/75 PASS** |
| T2 | Smoke tests: ≥8 groups | ✅ PASS | **59/59 PASS** |
| T3 | Smoke test runs as both script and module | ✅ PASS | Fixed during review cycle |
| T4 | Negative test cases for evidence mismatch | ✅ PASS | `test_incident.py` rejects mismatched propagated fields |
| T5 | Test commands documented and reproducible | ✅ PASS | See §4 below |

### 2.9 Review Findings Resolved

| # | Finding (from Codex review) | Status | Resolution |
|---|---|---|---|
| F1 | `create_postmortem()` only checked `incident_id` existence, not evidence match | ✅ FIXED | Full propagated evidence set now required to match exactly |
| F2 | `smoke_test_incident.py` failed on direct execution | ✅ FIXED | `sys.path` corrected for both script and module execution |
| F3 | Postmortem missing `deployment_plan_id`, `persona_capital_binding_id`, `runtime_id` | ✅ FIXED | All three fields added to Postmortem dataclass and schema |

---

## 3. Readiness Verdict

**INC-001 is READY for `done`.**

All acceptance criteria are satisfied:

| Criterion | Met? |
|---|---|
| Incident and postmortem objects attach to stage (`deployment_stage`) | ✅ |
| Incident and postmortem objects attach to binding (`binding_id` → RuntimeBinding) | ✅ |
| Incident and postmortem objects attach to lineage refs (`incident_id`, `linked_evolution_decision_id`) | ✅ |
| Evidence propagation enforced (9 fields, exact match required) | ✅ |
| Review findings resolved (3/3) | ✅ |
| Tests passing (75/75 unit, 59/59 smoke) | ✅ |
| Review approved by Codex | ✅ |

**No open blockers or deferred items.**

---

## 4. Reproducible Verification

Run these commands from the repo root to verify:

```bash
# Unit tests
python3 -m unittest services.incident.test_incident

# Smoke test — direct script execution
python3 services/incident/smoke_test_incident.py

# Smoke test — module execution
python3 -m services.incident.smoke_test_incident
```

Expected results:
- Unit tests: **75/75 PASS**
- Smoke checks: **59/59 PASS**

---

## 5. Files Referenced

### Canonical Artifacts (read-only, not modified by this sidecar)
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`

### Parent Task Implementation (read-only)
- `services/incident/contract.md`
- `services/incident/incident.py`
- `services/incident/incident_case.schema.json`
- `services/incident/postmortem.schema.json`
- `services/incident/test_incident.py`
- `services/incident/smoke_test_incident.py`

### Review Record
- `services/incident/review_inc001_codex_approved_zh.md`

### This Sidecar (support artifact)
- `support/sidecars/INC-001/INC-001-SIDECAR-ACCEPTANCE.md`

---

## 6. Reviewer Disposition and Parent-owner Handoff

Codex reviewed this sidecar packet after the sidecar review assignment was auto-reassigned from Claude to Codex at `2026-04-10T14:04:27Z`.

This review confirms that:

1. **The support packet matches the current parent-task state** — `INC-001` remains `review_approved` with Codex as reviewer and Claude as owner.
2. **The acceptance narrative is backed by the implementation and review record** — schema, dataclass, contract, and review notes all align on propagated evidence enforcement.
3. **The verification steps are reproducible now** — this review reran `python3 -m unittest services.incident.test_incident`, `python3 services/incident/smoke_test_incident.py`, and `python3 -m services.incident.smoke_test_incident`, confirming `75/75` unit tests and `59/59` smoke checks.
4. **This sidecar stays within slice limits** — only support material was updated; no canonical truth or runtime/governance implementation was modified.

**Recommended next step**: Qwen can mark `INC-001-SIDECAR-ACCEPTANCE` as complete, and Claude can absorb this packet if useful when closing `INC-001` from `review_approved` to `done`.

---

*Generated by Qwen and review-cleaned by Codex as a sidecar acceptance_packet helper for INC-001. This file is a support artifact and does not modify canonical truth.*
