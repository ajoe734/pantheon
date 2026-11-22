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
2. **Authoring vs. Binding vs. Strategy Style vs. Registry Admission vs. Allocation Artifacts**:
   - **Financial RiskPolicy Authoring**: Owned by the Capital/Governance risk policy domain (`services/capital/risk_policy.py`, `services/control-plane/governance/risk_policy.py`, policy files in `CAPITAL_RISK_POLICY_DIR`, and Governance `ApprovalDecision` targets `capital_pool_activation` / `pool_policy_parameter_proposal` decided by two distinct authorized reviewers (`governance_reviewer` / `risk_owner` / `governance_committee`)).
   - **Financial RiskPolicy Binding**: Executed by `services/capital/main.py` (`POST /api/capital-pools`, `PATCH /api/capital-pools/{pool_id}/status`) guarded by `CapitalGuard.authorize`, coordinated by `PersonaProvisioningCoordinator` (`_coordinate_capital_pool`, `repair_paper_pool_risk_policy`), and enforced at runtime by `resolve_bound_pool_risk_policy`, `ExpressionInputsProvider`, and `PaperExecutionAlgorithm.EvaluateOrderRiskGate`.
   - **Strategy Risk Style (Distinct)**: `StrategyArtifact.expression_policy.risk_profile="low"` and `strategy_logic.risk_limits` (`max_gross_exposure=1.0, max_net_exposure=1.0, max_position_weight=0.25`) describe strategy execution style, not the `CapitalPool`'s financial `RiskPolicy`.
   - **Registry Admission (Distinct)**: Governance `ApprovalDecision` with `target_type="registry_entry"`, `risk_level="low"` decided by `automated_gate` (`pantheon-dev-paper-provisioner`) is a 24h candidate admission grant consumed by `advance_artifact_state`; it does not authorize `capital_pool_activation`.
   - **Allocation Artifacts (Distinct)**: `AllocationPolicyArtifact` and `rebalance_apply` govern sleeve/persona weight rebalances within a pool, not the pool's `RiskPolicy` exposure ceiling.

---

## 2. Actual Hosted Dev State (`actual_hosted_observation`)

On hosted dev (`d585efa53fb213e63726c2a53c07234d9219a38b`, observed `2026-11-22T06:26:07Z`):

- **Policy Files on Hosted Containers** (`pantheon-capital-1`, `pantheon-operator-bff-1`, `pantheon-paper-signal-producer-1`):
  - `CAPITAL_RISK_POLICY_DIR=""`, `/app/config/risk_policies` and `/app/services/capital/risk_policies` do not exist (`0` `.json` policy files).
- **Governance Decisions on Hosted Dev**:
  - `0` `capital_pool_activation` decisions and `0` `pool_policy_parameter_proposal` decisions exist.
- **Three Active Dev Paper Pools**:
  1. `pool-persona-paper-06d4775a9b6a8f63b311` (`persona-06d4775a9b6a`, 澄川 凛, `TWD`, `budget=100000.0`)
  2. `pool-persona-paper-039ad01f8bcd2b4aa466` (`persona-039ad01f8bcd`, 沈 弈謙, `TWD`, `budget=100000.0`)
  3. `pool-persona-paper-a81b4fd15944698341aa` (`persona-a81b4fd15944`, Rhea Vance, `USD`, `budget=100000.0`)
  - All three remain `status="unresolved_profile_mapping"`, `absence_source="policy_definition_and_approval_absent"`, `allow_new_or_increased_risk=false`, `allow_risk_reducing_actions=true`, awaiting owner disposition in `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122`.

---

## 3. Auto-Resolution & Actionable Owner Routing (`local_test_fixture_not_hosted_state`)

1. **Bounded Auto-Resolution by `(tenant_id, requested_profile, stage)`**:
   - `discover_approved_profile_risk_policy` in `services/capital/risk_policy.py` performs bounded discovery across `CAPITAL_RISK_POLICY_DIR` (capped at `MAX_POLICY_CATALOG_FILES = 64`) and bounded target-scoped Governance lookups (`list_for_target`, capped at `MAX_BOUNDED_APPROVAL_CANDIDATES = 16`).
   - Resolves an applicable approved policy when uniquely established (either a profile-mapped catalog policy with valid Governance `ApprovalDecision` or a pool-targeted `capital_pool_activation` `ApprovalDecision` with any valid `decision_id`).
   - Persists and reads back `risk_policy_ref`, `metadata.risk_policy` (`risk_policy_id`, `version`, limits), `metadata.profile_mapping_approval_id`, and `approval_digest`.
   - Once `profile_mapping_approval_id` is persisted on the pool, runtime order evaluation (`resolve_bound_pool_risk_policy`) performs **1 exact-ID `GET` lookup** and **0 list scans**.
   - Fails closed on absent (`policy_definition_and_approval_absent`), unapproved (`policy_approval_missing`), pending (`policy_approval_pending`), ambiguous (`policy_candidates_ambiguous`), revoked (`policy_approval_revoked`), expired (`policy_approval_expired` / `policy_expired_or_stale`), superseded (`policy_approval_superseded`), rejected (`policy_approval_rejected`), or cross-tenant (`cross_tenant_policy_or_approval_rejected`) matches. Never infers a mapping from policy labels/names or treats an unapproved generic 2.0 policy as `low`.
2. **Actionable Routing on Real Absence**:
   - `PersonaProvisioningCoordinator._route_or_reuse_risk_policy_owner_decision_request` computes a deterministic proposal ID `apv-risk-profile-<hash>`, performs a GET-first check in Governance, reuses any existing `proposed`/`under_review` proposal on retry with zero duplicate writes, respects explicit non-approval (`decision="rejected"`), and routes a `capital_pool_activation` proposal (`risk_level="medium"`) to the Governance Human Inbox (`/management/approvals?approval={decision_id}`) linked to `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122` and `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`.
   - Any active-looking provisional pool record encountered during an unresolved `coordinate()` pass is transitioned to `status="suspended"` with `provisional_unresolved_risk_policy=True` so it cannot add risk, and is reactivated to `status="active"` once the owner approves the policy.
