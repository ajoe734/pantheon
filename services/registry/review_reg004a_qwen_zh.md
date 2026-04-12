# REG-004 Acceptance & Compatibility Audit (v2)

**Task:** REG-004A
**Author:** Qwen
**Reviewer:** Codex
**Date:** 2026-04-09
**Status:** Complete — corrected acceptance checklist, schema drift analysis, and compatibility audit

---

## 1. Task Scope

REG-004 (owner: Codex) splits `artifact_state` and `deployment_stage` across registry contracts,
replacing the legacy single-axis `lifecycle_state` / `promotion_state` model with the canonical
two-axis model defined in `TARGET_ARCHITECTURE.md`:

- **artifact_state**: `draft`, `candidate`, `approved`, `retired`
- **deployment_stage**: `none`, `paper`, `canary`, `live`, `frozen`

REG-004A is the parallel acceptance and compatibility audit. Its deliverables are:

1. A written acceptance checklist for REG-004's contract-level scope
2. An enumeration of schema drift and compatibility risks
3. Clear separation between this-round acceptance and follow-on migration work

---

## 2. Acceptance Checklist for REG-004 (Contract-Only Scope)

REG-004's scope is **contract alignment**, not full code migration. The contract defines the
canonical target; the compatibility window allows downstream consumers to continue emitting
legacy fields until `GOV-001`, `DEP-001`, and execution-side migration land.

### 2.1 Contract-Level Checks (`services/registry/contract.md`)

| # | Criterion | Status | Notes |
|---|---|---|---|
| A1 | `artifact_state` enum matches TARGET_ARCHITECTURE: `draft`, `candidate`, `approved`, `retired` | **PASS** | Contract §3 defines exactly these four values |
| A2 | `deployment_stage` enum matches TARGET_ARCHITECTURE: `none`, `paper`, `canary`, `live`, `frozen` | **PASS** | Contract §4 defines all five values |
| A3 | Allowed transitions are correct for artifact_state only | **PASS** | Contract defines `draft→candidate`, `candidate→approved`, `approved→retired`, plus direct retire from draft/candidate |
| A4 | Deployment stage transitions are NOT defined inside the registry contract | **PASS** | Contract correctly scopes registry to artifact_state; deployment_stage is derived (see §4 ownership rules, §8 `resolve_deployment_view`) |
| A5 | Registry entry model uses `artifact_state` (not `lifecycle_state`) | **PASS** | Contract §5 entry model table lists `artifact_state` as required; `lifecycle_state` no longer appears |
| A6 | Registry entry model has `deployment_summary` as derived non-authoritative view (not top-level `deployment_stage`) | **PASS** | Contract §5 has `deployment_summary` with suggested shape including `current_stage` |
| A7 | Execution projection mapping uses `artifact_state` + deployment/runtime read model → `deployment_stage` | **PASS** | Contract §7 projection table maps correctly |
| A8 | Loader-facing rejection rules use `artifact_state=approved` + exact deployment_stage match | **PASS** | Contract §7 loader rules are explicit |
| A9 | Compatibility window is explicitly documented | **PASS** | Contract §7 "Compatibility window" section lists `REG-002`/`REG-003`/`EX-001` legacy fields and defers migration to follow-on tasks |
| A10 | Minimal operations use new vocabulary (`advance_artifact_state`, `resolve_latest_approved`, `resolve_deployment_view`) | **PASS** | Contract §8 uses canonical operation names |

### 2.2 Machine-Readable Schema Checks (`services/registry/registry_entry_schema.json`)

| # | Criterion | Status | Notes |
|---|---|---|---|
| B1 | Schema uses `artifact_state` (not `lifecycle_state`) as required field | **PASS** | `required` array includes `artifact_state`; property type is string with enum `[draft, candidate, approved, retired]` |
| B2 | Schema does NOT require top-level `deployment_stage` (belongs to deployment/runtime) | **PASS** | Schema correctly has `deployment_summary` as optional derived object with `current_stage` enum `[none, paper, canary, live, frozen]` |
| B3 | `deployment_summary` is marked non-authoritative | **PASS** | Schema has `$comment`: "Derived deployment read model only. Deployment stage authority lives outside the registry entry." |
| B4 | All contract §5 fields are present in schema properties | **PASS** | `registry_id`, `artifact_type`, `strategy_id`, `version`, `artifact_state`, `lineage`, `storage_ref`, `checksum`, `producer_run_id`, `evaluation_summary`, `approval_decision_id`, `approver`, `approved_at`, `rollback_target`, `deployment_summary`, `metadata` all present |
| B5 | `$schema` draft version is valid | **PASS** | Uses `draft-07`, consistent with other schemas in the project |

### 2.3 Promotion Gate Documentation Checks (`services/registry/promotion/README.md`)

| # | Criterion | Status | Notes |
|---|---|---|---|
| C1 | README documents canonical `artifact_state` transition rules | **PASS** | "Canonical Artifact-State Rules" section lists `draft→candidate→approved→retired` |
| C2 | README separates deployment stage from artifact state | **PASS** | "Deployment Stage Is Separate" section explicitly states `paper/canary/live/frozen` belong to deployment/runtime |
| C3 | README documents compatibility window for legacy code | **PASS** | "Execution Projection" section notes `build_execution_projection()` still emits legacy `promotion_state`; "Legacy live compatibility" section documents current gate.py behavior |
| C4 | README correctly scopes what is done vs. follow-on | **PASS** | "Scope" section clearly states: implementation still uses legacy model; README documents canonical target; follow-on tasks (`GOV-001`, `DEP-001`, execution-side migration) will bring code into alignment |

### 2.4 Follow-On Items (NOT REG-004 Blockers)

These are correctly identified as follow-on work in the contracts. They are **not** acceptance
failures for REG-004 — they are explicitly deferred to later tasks:

| Item | Current State | Deferred To | Notes |
|---|---|---|---|
| `promoted_artifact_metadata.schema.json` still uses `promotion_state` | Legacy envelope | `GOV-001` / `DEP-001` / execution-side migration | Contract §7 compatibility window explicitly allows this |
| `lineage/contract.md` still references `promotion_state` | Legacy envelope | Same as above | Compatibility note in doc header acknowledges this |
| `artifact-loader/contract.md` still checks `promotion_state` | Legacy envelope | Same as above | Compatibility note in doc header acknowledges this |
| `gate.py` / `cli.py` / tests still use legacy model | Legacy implementation | Same as above | README documents this as current implementation note |
| `ApprovalDecision` integration | Not yet first-class | `GOV-001` | Contract has `approval_decision_id` as optional field awaiting GOV-001 |
| `DeploymentPlan` integration | Not yet defined | `DEP-001` | Contract references `DeploymentPlan` as future deployment owner |

---

## 3. Compatibility Window Summary

REG-004 defines a **two-axis canonical model** (`artifact_state` + `deployment_stage`) but
correctly maintains a **compatibility envelope** for downstream consumers that still emit and
consume the legacy single-axis model (`lifecycle_state` / `promotion_state`).

### 3.1 What Is Canonical Now

| File | Canonical Vocabulary | Status |
|---|---|---|
| `services/registry/contract.md` | `artifact_state` + `deployment_stage` | ✅ Aligned |
| `services/registry/registry_entry_schema.json` | `artifact_state` + `deployment_summary` | ✅ Aligned |
| `services/registry/promotion/README.md` | Documents canonical target + legacy gap | ✅ Aligned |
| `TARGET_ARCHITECTURE.md` | `artifact_state` + `deployment_stage` | ✅ Source of truth |

### 3.2 What Is Still Legacy (Compatibility Envelope)

| File | Legacy Vocabulary | Why It's OK for Now |
|---|---|---|
| `services/registry/lineage/promoted_artifact_metadata.schema.json` | `promotion_state: [candidate, paper, live, retired]` | Execution-facing schema; migration deferred to follow-on tasks. Contract §7 explicitly allows this. |
| `services/registry/lineage/contract.md` | References `promotion_state` | Describes legacy execution-facing envelope; header acknowledges compatibility note |
| `services/execution/artifact-loader/contract.md` | Checks `promotion_state=paper\|live` | Consumer contract; header acknowledges compatibility envelope |
| `services/registry/promotion/gate.py` + tests | `PromotionState` enum with `lifecycle_state` | Current implementation; README documents gap |
| `services/registry/promotion/cli.py` | `--to paper\|live` CLI | Current implementation; README notes canonical target is artifact-state-only |

### 3.3 Vocabulary Mapping (For Reference During Migration)

| Legacy field | Legacy value | New fields |
|---|---|---|
| `lifecycle_state` / `promotion_state` | `draft` | `artifact_state=draft`, `deployment_stage=none` |
| `lifecycle_state` / `promotion_state` | `candidate` | `artifact_state=candidate`, `deployment_stage=none` |
| `lifecycle_state` / `promotion_state` | `paper` | `artifact_state=approved`, `deployment_stage=paper` |
| `lifecycle_state` / `promotion_state` | `live` | `artifact_state=approved`, `deployment_stage=live` |
| `lifecycle_state` / `promotion_state` | `retired` | `artifact_state=retired`, `deployment_stage=none` |
| (new) | — | `deployment_stage=canary` and `deployment_stage=frozen` have no legacy equivalent |
| (new) | — | `artifact_state=approved` with `deployment_stage=none` is a new valid combination |

### 3.4 Compatibility Risks During Migration

| Risk | Severity | Description | Mitigation |
|---|---|---|---|
| R1: Downstream consumers read `promotion_state` exclusively | **High** | Loader and gate code still check `promotion_state=paper\|live`. If new code paths only write `artifact_state` + `deployment_stage` without the legacy alias, downstream breaks. | REG-004 contract requires dual-read during migration window (§7). Follow-on migration tasks must maintain the alias until all consumers switch. |
| R2: `paper` and `live` are no longer valid artifact_state values | **Medium** | Any code comparing `artifact_state == "paper"` will silently fail. | Clear contract documentation (§3, §4) + migration mapping in §7. Follow-on tasks must audit comparison sites. |
| R3: `approved` is a new state value | **Medium** | Consumers unfamiliar with `approved` may reject it as invalid. | Already in `registry_entry_schema.json` enum. Follow-on migration must update consumer allowlists. |
| R4: CLI `--to` argument ambiguity | **Medium** | `--to paper` currently implies both state advance and stage bind. After split, CLI must disambiguate. | Contract §8 separates `advance_artifact_state()` from deployment binding. Future deployment-plan CLI will handle stage changes. |
| R5: No tests for dual-read compatibility path yet | **Medium** | If tests only cover legacy or only cover new vocabulary, regressions in the alias path may go unnoticed. | Follow-on migration tasks should add explicit dual-read test cases. Not a REG-004 blocker since REG-004 is contract-first. |

---

## 4. Ownership Model Summary

REG-004 correctly establishes the following ownership boundaries:

| Object | Owner | Reason |
|---|---|---|
| `artifact_state` | Registry (§5, §8) | Registry is the source of truth for artifact governance lifecycle |
| `deployment_summary.current_stage` | Registry (derived, non-authoritative) | Cached read-model view; authority lives in deployment/runtime objects |
| `deployment_stage` (canonical) | DeploymentPlan / DEP-001 | Deployment stage is a separate runtime concern, not a registry lifecycle field |
| `RuntimeBinding` | Execution / runtime-manager | Records what is actually running in LEAN |
| `ApprovalDecision` | GOV-001 | Canonical approval authority (not yet first-class) |

The registry may attach a `deployment_summary` to an entry for convenience, but it must not
treat that summary as source truth. This is correctly encoded in both `contract.md` §5 and
`registry_entry_schema.json` (the `$comment` on `deployment_summary` explicitly marks it as
derived and non-authoritative).

---

## 5. Conclusion

| Category | Count |
|---|---|
| Acceptance checks PASS (REG-004 contract scope) | 18 |
| Acceptance checks FAIL (within REG-004 scope) | 0 |
| Follow-on items correctly deferred | 6 |

**REG-004's contract-level work is complete and aligned with TARGET_ARCHITECTURE.md.**

The registry contract (`contract.md`) and machine-readable schema (`registry_entry_schema.json`)
both correctly split `artifact_state` (governance lifecycle) from `deployment_stage` (runtime
placement). The promotion README documents the canonical target and the current compatibility
gap. The compatibility window is explicit about which downstream consumers still use legacy
vocabulary and which follow-on tasks will absorb the migration.

**Recommendation:** REG-004 can be marked `done` at the contract level. The remaining
implementation migration (gate.py, CLI, tests, promoted_artifact_metadata.schema.json, loader
contract, lineage contract) belongs to follow-on tasks and should not block this task's
completion.
