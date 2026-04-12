# Work Rebaseline

Last updated: 2026-04-09
Status: historical rebasing note after clarifying that several architecture boxes refer to real upstream OSS projects
Tier: L3 Supporting Design & Migration
Scope: historical task-interpretation reset and audit rationale from the pre-canonical-tier cutover period
Conflict rule: this file explains why the old work model changed, but active planning truth now lives in `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, and the L1 policy set

## 1. Why This Rebaseline Exists

We found a real planning drift:

- several architecture boxes named actual OSS frameworks or repos
- some work items were being read as conceptual local implementation tasks
- but the intended meaning was upstream integration plus local governance/adapters

This file resets the work model so every LLM is checking against the same assumption.

## 2. Work Buckets

### A. Local Foundation Work

These tasks are still valid as local repo work:

- collaboration/status/dashboard
- signal schema and signal-store contracts
- local review automation helper under `tools/openclaw-local/`
- local governance, routing, and registry contracts

These are not upstream integrations by themselves.

### B. Local Contract and Adapter Work

These tasks remain useful, but should now be interpreted as local wrappers around upstream systems:

- `OC-001`
- `OC-003`
- `REG-001`
- `FB-001`
- `LP-001` contract draft

These tasks are **not enough** to say the upstream framework is integrated.

### C. Upstream OSS Integration Work

These now require explicit upstream selection, version pinning, adapter work, and smoke tests:

- `OpenClaw`
- `DSPy`
- `TRL`
- `Qlib`
- `FinRL`
- `RLlib`
- `Ray Tune`
- `imitation`
- `MLflow`
- `W&B`

The tracking checklist is:

- `OSS_INTEGRATION_CHECKLIST.md`

## 3. Rebased Task Interpretation

### OpenClaw Tasks

- `OC-001`: map upstream OpenClaw permissions into our deny-first local model
- `OC-002`: integrate or configure upstream OpenClaw cron/workflow entrypoints
- `OC-003`: map upstream OpenClaw outputs into local `StrategySpec` and workflow handoff objects

### Research Tasks

- `RS-001`: integrate governed ingestion adapters from structured APIs
- `RS-002`: normalize governed research outputs into `StrategySpec`
- `RS-003`: implement replication gate before registry entry

### Learning Tasks

- `LP-001`: integrate upstream `DSPy`
- `LP-002`: integrate upstream `imitation`
- `LP-003`: integrate `MLflow` or `W&B`
- `LP-004`: integrate governed `TRL` only after feedback controls exist
- `LP-005`: integrate `FinRL` or `RLlib + Tune` only if RL becomes justified

## 4. Audit Pass Required From Every LLM

Each LLM should now inspect previously developed work against the corrected model.

### Claude Audit Scope

Focus:

- execution and control-plane work
- whether prior work incorrectly assumed local greenfield behavior where upstream integration or LEAN-native constraints should apply

Inspect at least:

- `P3-001`
- `P4-001`
- `EX-001`
- `OC-001`
- `OC-003`

Expected output:

- what is still valid
- what is only a local contract and not real integration
- what follow-up integration tasks are now mandatory

### Gemini Audit Scope

Focus:

- runtime, promotion, cron, and experiment backend work
- whether implementation tasks now need explicit upstream dependency selection and smoke tests

Inspect at least:

- `EX-001`
- `REG-002`
- `OC-002`
- `RS-001`
- `LP-003`
- `LP-004`
- `LP-005`

Expected output:

- missing upstream dependency choices
- missing packaging/runtime integration steps
- missing smoke-test paths

### Grok Audit Scope

Focus:

- research and source-handling assumptions
- whether prior work respects structured-source ingestion and coding-safe scope boundaries

Inspect at least:

- `RS-000`
- `RS-001`
- `RS-002`
- `RS-003`

Expected output:

- whether source assumptions are sound
- whether browser-first / coding-assist boundaries need correction
- what research adapters must exist before downstream work continues

### Codex Audit Scope

Focus:

- contracts, schemas, registry, and learning-framework assumptions
- whether wording, acceptance criteria, and artifacts now track real upstream integration requirements

Inspect at least:

- `REG-001`
- `FB-001`
- `LP-001`
- `LP-002`
- `REG-003`

Expected output:

- which contracts are still good
- where they stop short of real framework integration
- what checklist items are still missing

## 5. Required Audit Output Format

Each LLM audit should produce:

1. a short `audit.md` note in its area
2. a status update via `scripts/ai-status.sh`
3. concrete findings in one of these forms:
   - valid as-is
   - valid but only local wrapper/contract
   - missing upstream integration step
   - needs new spike task

## 6. Immediate Rule Going Forward

No task that names an upstream OSS component should be treated as fully complete unless the checklist below is materially addressed:

- source selected
- version pinned
- dependency or repo path added
- local adapter boundary defined
- smoke test exists or is explicitly planned
