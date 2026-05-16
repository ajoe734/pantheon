# OpenClaw StrategySpec and Workflow Handoff Contract

**Task:** OC-003  
**Owner:** Codex  
**Reviewer:** Claude  
**Status:** DRAFT — ready for orchestration and governance review

---

## 1. Purpose

OpenClaw needs a stable object model for what it discovers, proposes, and hands to downstream systems.

Without that object model:

- research ingestion produces ad hoc notes
- registry entries lack a normalized upstream source
- approval flows cannot tell what is being approved
- execution-facing artifacts become difficult to trace back to orchestration intent

This contract defines two related objects:

1. `StrategySpec`
2. `WorkflowHandoff`

`StrategySpec` is the normalized strategy description.
`WorkflowHandoff` is the governed envelope used to pass a spec or related work unit between workflow stages.

Machine-readable schemas:

- `services/control-plane/specs/strategy_spec.schema.json`
- `services/control-plane/specs/workflow_handoff.schema.json`

---

## 2. StrategySpec

### 2.1 What StrategySpec is

`StrategySpec` is the canonical description of a strategy idea before it becomes a registry entry or execution bundle.

It is not:

- a broker order
- a live artifact
- a training checkpoint

It is the normalized strategy intent that later systems can reason about.

### 2.2 Minimum required sections

| Section | Required | Purpose |
|---|---|---|
| `strategy_id` | yes | stable strategy family id |
| `title` | yes | human-readable label |
| `hypothesis` | yes | thesis the strategy is trying to express |
| `objective` | yes | what the strategy optimizes for |
| `market_scope` | yes | symbols, venue, asset classes, cadence |
| `data_dependencies` | yes | datasets, feature sets, or research refs |
| `execution_profile` | yes | sizing mode, signal contract version, execution mode expectations |
| `evaluation_plan` | yes | metrics and gate criteria for replication/paper/live |
| `governance` | yes | policy, approval, and risk hints |
| `provenance` | yes | where this spec came from |

### 2.3 Registry-ready requirement

Every StrategySpec must be usable as a registry precursor.

That means it must already contain enough information to support:

- registry linkage (`strategy_id`)
- lifecycle staging hints (`draft` or `candidate`)
- lineage/provenance references
- ownership/governance metadata

This allows `RS-002` to normalize research directly into a governed object instead of inventing a second format later.

### 2.4 Evidence and code reference lineage

StrategySpec can carry optional evidence/code linkage fields when a source seed
has governed source material:

| Field | Required | Purpose |
|---|---|---|
| `evidence_refs[]` | no | evidence bundle, evidence item, source record, citation, experiment artifact, or registry-entry refs that justify the StrategySpec |
| `code_refs[]` | no | allowlisted repository/path/commit/symbol/line refs that identify source implementation or prototype material |

These refs are lineage inputs only. They do not grant registry write authority,
experiment launch authority, deployment-plan authority, broker access, or order
routing. Implementations that build these refs must preserve
`provenance.source_refs` and reject source/evidence objects outside the source
seed lineage.

---

## 3. WorkflowHandoff

### 3.1 What WorkflowHandoff is

`WorkflowHandoff` is the transport envelope for moving governed work between OpenClaw stages.

Examples:

- research ingestion -> normalization
- normalization -> replication
- replication -> registry submission
- registry -> approval workflow

### 3.2 Minimum required sections

| Section | Required | Purpose |
|---|---|---|
| `handoff_id` | yes | unique id for this handoff |
| `handoff_type` | yes | what kind of payload is being handed off |
| `from_stage` | yes | source workflow stage |
| `to_stage` | yes | destination workflow stage |
| `created_at` | yes | RFC3339 timestamp |
| `strategy_spec` | yes | normalized StrategySpec payload or ref |
| `registry_hints` | yes | registry-ready metadata for downstream admission |
| `governance_context` | yes | approval / policy / execution context hints |
| `provenance` | yes | source task, channel, persona, or cron origin |

### 3.3 Handoff types

Minimum v1 handoff types:

| Handoff type | Meaning |
|---|---|
| `strategy_spec` | normalized strategy proposal |
| `research_package` | research material ready for replication |
| `registry_submission` | candidate ready to be admitted into REG-001 / REG-002 |
| `approval_request` | promotion or governance decision package |

---

## 4. Registry Hints

The handoff envelope must already speak the language of REG-001.

Minimum `registry_hints` fields:

| Field | Required | Description |
|---|---|---|
| `artifact_type` | yes | usually `strategy_spec`, but can evolve later |
| `initial_lifecycle_state` | yes | `draft` or `candidate` |
| `lineage_ref` | no | upstream source run or registry parent |
| `producer_run_id` | no | generating workflow or replication run |
| `source_dataset_refs` | no | datasets or feature refs relevant to the submission |

This is the bridge between orchestration outputs and registry entry creation.

---

## 5. Governance Context

OpenClaw should not hand downstream systems a bare strategy object without governance context.

Minimum `governance_context` fields:

| Field | Required | Description |
|---|---|---|
| `policy_id` | no | permission or governance policy in effect |
| `approval_required` | yes | whether downstream step requires approval |
| `execution_context` | yes | `research`, `paper`, or `live` |
| `risk_profile` | no | max drawdown / exposure or other hints |

This keeps `OC-001`, `P4-001`, and later approval workflows aligned.

---

## 6. Review Focus

Claude should review this contract for:

- whether the StrategySpec boundary is clear enough that OpenClaw is not leaking execution detail too early
- whether WorkflowHandoff carries enough governance context for later approval and registry steps
- whether the registry hints are specific enough for REG-001 / REG-002 without over-coupling orchestration to storage internals
