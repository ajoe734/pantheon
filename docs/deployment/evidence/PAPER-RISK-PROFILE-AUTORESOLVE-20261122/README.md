# PAPER-RISK-PROFILE-AUTORESOLVE-20261122 Evidence & Owner Disposition Packet

- **Task ID**: `PAPER-RISK-PROFILE-AUTORESOLVE-20261122`
- **Delivery State**: `open_pr_pending_review_and_merge` (not yet `merged`, `deployed`, or `accepted`)
- **Problem Register Class**: RC2 (`docs/operations/problem-register.md` row 217 — persona-loop paper risk policy binding chain)
- **Product Policy Owner Disposition Task**: `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122`
- **Downstream Hosted Acceptance Proof Task**: `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`

---

## 1. RC2 Residual Reproduction on Hosted Build `d585efa53f` (`actual_hosted_observation`)

1. **Normal `createPersona` → `POST /bff/personas` → Provisioning → Capital Owner Readback Trace**:
   - The frontend `createPersona` flow sends `POST /bff/personas` with `risk="low"`, `risk_policy_ref=null`, and no internal policy or Governance decision identifiers (`no risk_policy`, `no profile_mapping_approval_id`, `no approval_decision_id`).
   - On hosted build `d585efa53f` (after `#7726` / `PAPER-RISK-POLICY-BINDING-20261121`):
     - `PersonaProvisioningCoordinator._resolve_requested_pool_risk_policy` constructed the generic 2.0 baseline (`_default_paper_pool_risk_policy`: `rp-pool-persona-paper-<token>`, `gross_limit=2.0, net_limit=2.0, max_leverage=2.0`) and called `resolve_pool_risk_policy_binding` without searching `CAPITAL_RISK_POLICY_DIR` or bounded target-scoped Governance approvals.
     - `PersonaProvisioningCoordinator._coordinate_capital_pool` set `effective_approval_id = candidate_approval_id or f"apv-capital-pool-{ids.capital_pool_id}"`, assuming a deterministic approval ID instead of persisting a discovered arbitrary `decision_id`.
     - `services/capital/risk_policy.py::_verify_policy_owner_approval_decision` only checked `reader.get(f"apv-capital-pool-{resolved_pool_id}")` when no explicit `approval_decision_id` was supplied on the request or pool record.
2. **Authoring vs. Binding vs. Strategy Style vs. Registry Admission vs. Allocation Artifacts vs. Lesson Parameter Proposals**:
   - **Financial RiskPolicy Authoring**: Owned by the Capital/Governance risk policy domain (`services/capital/risk_policy.py`, `services/control-plane/governance/risk_policy.py`, policy files in `CAPITAL_RISK_POLICY_DIR`, and Governance `ApprovalDecision` target `capital_pool_activation` with `subject.risk_direction="increase"` decided by two distinct authorized reviewers (`governance_reviewer` / `risk_owner` / `governance_committee`)).
   - **Financial RiskPolicy Binding**: Executed by `services/capital/main.py` (`POST /api/capital-pools`, `PATCH /api/capital-pools/{pool_id}/status`) guarded by `CapitalGuard.authorize`, coordinated by `PersonaProvisioningCoordinator` (`_coordinate_capital_pool`, `repair_paper_pool_risk_policy`), and enforced at runtime by `resolve_bound_pool_risk_policy`, `ExpressionInputsProvider`, and `PaperExecutionAlgorithm.EvaluateOrderRiskGate`.
   - **Strategy Risk Style (Distinct)**: `StrategyArtifact.expression_policy.risk_profile="low"` and `strategy_logic.risk_limits` (`max_gross_exposure=1.0, max_net_exposure=1.0, max_position_weight=0.25`) describe strategy execution style, not the `CapitalPool`'s financial `RiskPolicy`.
   - **Registry Admission (Distinct)**: Governance `ApprovalDecision` with `target_type="registry_entry"`, `risk_level="low"` decided by `automated_gate` (`pantheon-dev-paper-provisioner`) is a 24h candidate admission grant consumed by `advance_artifact_state`; it does not authorize `capital_pool_activation`.
   - **Allocation Artifacts (Distinct)**: `AllocationPolicyArtifact` and `rebalance_apply` govern sleeve/persona weight rebalances within a pool, not the pool's `RiskPolicy` exposure ceiling.
   - **Lesson Parameter Proposal (`pool_policy_parameter_proposal`, Distinct)**: Per `services/governance/contract.md:70-76`, `pool_policy_parameter_proposal` (`subject` fields `proposal_id`, `pool_id`, `lesson_reference`, `parameter_diffs`) is a lesson-derived parameter proposal record only and is rejected as `CapitalPool` activation authority.

---

## 2. Actual Hosted Dev State (`actual_hosted_observation`)

Credentials-scoped hosted dev observations (`d585efa53fb213e63726c2a53c07234d9219a38b` / `bac80d75690f74a351d5b49b036c07df1daaa55d`):
- `docs/deployment/evidence/PAPER-RISK-POLICY-PROVENANCE-20261121/policy-inventory.json`
- `docs/deployment/evidence/PAPER-RISK-POLICY-PROVENANCE-20261121/owner-readback.json`
- `docs/deployment/evidence/PAPER-RISK-POLICY-PROVENANCE-20261121/probe-hosted-owner.json`
- `/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121-followthrough-20261122.GsLzfRMc/risk-owner-readback.json`
- `/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121-closure-20261122.yjscFJxG/capital-governance-scope.json`
- `/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121-closure-20261122.yjscFJxG/bac80d756-hosted-risk-probe.json`

Summary of hosted dev facts:
- **Policy Files on Hosted Containers** (`pantheon-capital-1`, `pantheon-operator-bff-1`, `pantheon-paper-signal-producer-1`):
  - `CAPITAL_RISK_POLICY_DIR=""`, `/app/config/risk_policies` and `/app/services/capital/risk_policies` do not exist (`0` `.json` policy files).
- **Governance Decisions on Hosted Dev (`697` total in `tenant-dev`)**:
  - `registry_entry: 683`, `persona_lifecycle_transition: 8`, `evolution_execute: 2`, `evolution_proposal: 1`, `strategy_workshop: 1`, `capital_pool_activation: 1`, `capital_binding_activation: 1`, `pool_policy_parameter_proposal: 0`.
  - The single `capital_pool_activation` record on hosted dev is `decision_id="approval-paper-human-quorum-closeout-20261010-v1"` (`target_id="pool-paper-human-quorum-closeout-20261010-v1"`, `state="decided"`, `outcome="rejected"`, `subject_fields=["pool_id", "risk_direction"]`) — an unrelated rejected October decision.
  - The single `capital_binding_activation` record is `decision_id="apv-hosted-acceptance-20261003-fe-approval"` (`target_id="pcb-persona-paper-cf8fe181b7d7ff9b0097"`).
  - Applicable `capital_pool_activation` profile-mapping candidates for the 3 active dev paper pools: **`0`** (`policy_mapping_candidates: []`).
- **Three Active Dev Paper Pools**:
  1. `pool-persona-paper-06d4775a9b6a8f63b311` (`persona-2448c7053e33ea48da47`, `青禾｜2330 台股 Paper 學習 R3`, `TWD`, `budget=100000.0`, `runtime_id="rt-ad81501b"`, `binding_id="rb-eb74118dd3ca4b169496b1c239087512"`)
  2. `pool-persona-paper-039ad01f8bcd2b4aa466` (`persona-6507750312870d03a241`, `澄川｜2317 台股 Paper 學習 R2`, `TWD`, `budget=100000.0`, `runtime_id="rt-3252fa75"`, `binding_id="rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4"`)
  3. `pool-persona-paper-a81b4fd15944698341aa` (`persona-ee7112a829d38b9135ac`, `衡岳｜0050 台股 Paper 學習 R2`, `TWD`, `budget=100000.0`, `runtime_id="rt-3b65a843"`, `binding_id="rb-2b0df30559154a93902c209b0318d95c"`)
  - All three remain `status="unresolved_profile_mapping"`, `absence_source="policy_definition_and_approval_absent"`, `allow_new_or_increased_risk=false`, `allow_risk_reducing_actions=true`, awaiting owner disposition in `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122`.

---

## 3. Auto-Resolution, Fail-Closed Ambiguity, Actionable Owner Routing & Runtime Budget (`local_test_fixture_not_hosted_state`)

1. **Bounded Auto-Resolution & Fail-Closed Ambiguity by `(tenant_id, requested_profile, stage)`**:
   - `discover_approved_profile_risk_policy` in `services/capital/risk_policy.py` strips container approval hints per candidate and performs bounded discovery across `CAPITAL_RISK_POLICY_DIR` (capped at `MAX_POLICY_CATALOG_FILES = 64`) and bounded target-scoped Governance lookups (`list_for_target`, capped at `MAX_BOUNDED_APPROVAL_CANDIDATES = 16`).
   - Resolves an applicable approved policy only when uniquely established via `capital_pool_activation` with `risk_direction="increase"` and 2 distinct authorized deciders.
   - When multiple approved catalog policies or multiple approved decisions match, returns `status="ambiguous_profile_mapping"` with `candidate_policy=None`, `ambiguous_policy_ids`, and `ambiguous_approval_decision_ids`. `PersonaProvisioningCoordinator` preserves `ambiguous_profile_mapping` across `_resolve_requested_pool_risk_policy_with_provenance`, `coordinate()` create/resume (suspending any provisional active pool), and `repair_paper_pool_risk_policy()` without promoting any candidate or activating the pool.
   - Once `profile_mapping_approval_id` is persisted on the pool, runtime order evaluation (`resolve_bound_pool_risk_policy`) performs **1 exact-ID `GET` lookup** and **0 list scans**.
2. **Actionable Routing & Storage Manifest Budget (`docs/operations/storage-manifest.json`)**:
   - `PersonaProvisioningCoordinator._route_or_reuse_risk_policy_owner_decision_request` computes deterministic `decision_id = apv-risk-profile-<sha256[:20]>`, performs a GET-first check in Governance, reuses any existing `proposed`/`under_review` or `decided=rejected` record on retry with 0 duplicate writes, and emits 0 proposals on `ambiguous_profile_mapping`.
   - Incremental writer budgets registered in `docs/operations/storage-manifest.json` (`python3 scripts/check_storage_manifest.py` passed with `0` failures):
     - `/data/governance/approval_decisions.json` & `governance.approval_decisions`: `<= 3` keyed records (`~1.1 KB/record`, `~3.4 KB` total across 3 dev paper pools), `0 B/day` steady-state.
     - `audit.jsonl` (`owner=governance`) & `governance.audit_events`: `<= 3` `approval_decision_created` events (`~480-620 B/event`, `~1.6 KB` one-time across 3 pools), `0` on retry reuse or ambiguity, `0 B/day` steady-state.
     - `services/control-plane/bff/persona_provisioning.py::{self.table}`: in-place keyed JSONB update (`~0.65-1.3 KB` on existing row per `(tenant_id, idempotency_key)`), `0` new rows, `0 B/day` steady-state; pending `risk_policy_owner_decision_request` popped on resolution.
     - `/data/capital/capital_pools.json`, `capital.capital_pools`, `capital_audit.jsonl`, `capital.audit_events`: in-place keyed `PATCH /api/capital-pools/{pool_id}/status` (`~280-340 B` metadata, `1` `capital_pool_status_updated` event `~420 B` per actual mutation), `0 B/day` steady-state.
