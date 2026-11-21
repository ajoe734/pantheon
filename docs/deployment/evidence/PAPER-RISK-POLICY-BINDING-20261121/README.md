# PAPER-RISK-POLICY-BINDING-20261121 — Paper Pool Risk Policy Binding, Guard, and Repair Evidence

- **Task ID:** `PAPER-RISK-POLICY-BINDING-20261121`
- **Register Class:** `RC2` (Persona loop / paper risk policy binding)
- **Provenance Input:** `PAPER-RISK-POLICY-PROVENANCE-20261121` (`docs/deployment/evidence/PAPER-RISK-POLICY-PROVENANCE-20261121/`)
- **Owner:** `Antigravity2`
- **Reviewer:** `Codex2`
- **Delivery State:** `merged` (pending hosted dev deploy and post-deploy acceptance probe execution under `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`)

## 1. Provenance-Backed Root Causes and Implemented Repairs

### 1.1 Profile Translation vs. Pool-Level Effective Limits (`RC2`)

As established by `PAPER-RISK-POLICY-PROVENANCE-20261121`:

1. **Requested Profile Translation:**
   - `POST /bff/personas` accepts `risk` (or `risk_level` / `risk_profile.risk_level`) and `risk_policy_ref` / `riskPolicyRef`.
   - `services/control-plane/bff/personas/service.py::_persona_create_canonical_payload` now normalizes `riskPolicyRef` into `risk_policy_ref` even when an explicit `"risk_policy_ref": null` key is present in the payload, and normalizes `risk` from `risk_level` / `risk_profile.risk_level` when `risk` is omitted.
   - During artifact construction, the persona's requested risk profile translates to `StrategyArtifact.expression_policy.risk_profile` (`execution_style`) for signal expression.
2. **Where Effective Financial Limits and Policy Version Are Read:**
   - Pool-level financial limits (`gross_limit`, `net_limit`, `max_leverage`, `max_single_name_weight`, `allowed_stages`, `allowed_asset_classes`, `forbidden_asset_classes`, `drawdown_actions`, `liquidity_constraints`) and `version` are owned by `services/capital/risk_policy.py` (re-exported at `services/control-plane/governance/risk_policy.py`).
   - At runtime, `services/execution/lean_runtime/expression_inputs.py::ExpressionInputsProvider` and `services/execution/lean_runtime/paper_runtime.py::PaperExecutionAlgorithm` call `services/capital/risk_policy.py::resolve_bound_pool_risk_policy` against the bound `CapitalPool` record (`/api/capital-pools/{pool_id}`).
   - `resolve_bound_pool_risk_policy` gives an explicit `CapitalPool.risk_policy_ref` precedence over `CapitalPool.metadata.risk_policy`, and never reads risk policy limits from signal metadata.
3. **Whole-Pool Fallback Removal & Unapproved Profile Mapping Surfacing:**
   - Previously, `resolve_bound_pool_risk_policy` fell back to `normalize_risk_policy(pool_obj)` when a `CapitalPool` mapping had neither `risk_policy_ref` nor `metadata.risk_policy`, turning a policy-free pool record or empty `{}` `metadata.risk_policy` into `RiskPolicy(risk_policy_id="risk-policy-inline", status="active", gross_limit=None)`.
   - `resolve_bound_pool_risk_policy` and `resolve_pool_risk_policy_binding` now reject pool-envelope mappings without policy content (`status="missing"`, `reason_code="risk_policy_missing"`) and reject empty `{}` or malformed inline `risk_policy` objects (`status="invalid"`, `reason_code="risk_policy_invalid"`).
   - When `require_approved_profile_mapping=True` and no explicit approved `risk_policy` or `risk_policy_ref` is bound for the requested profile label (such as `risk="low"`), `resolve_pool_risk_policy_binding` and `PersonaProvisioningCoordinator.repair_paper_pool_risk_policy` do **not** invent arbitrary numeric single-name or cash-reserve percentages; they return `status="unresolved_profile_mapping"` (`reason_code="risk_policy_profile_mapping_unapproved"`) and surface `owner_decision_request` to `services/capital/risk_policy.py`.

### 1.2 Capital Guard, Approval Digest Binding, and Risk-Reducing Availability

1. **Approval Digest Includes Inline Policy Digest:**
   - `services/capital/main.py::pool_digest` now hashes `risk_policy_digest(pool.metadata["risk_policy"])` whenever an inline `metadata.risk_policy` mapping is present on a `CapitalPool`, binding `CapitalPool.approval_digest` to the exact financial policy contents while leaving pools without inline `metadata.risk_policy` byte-identical.
2. **Fail-Closed Paper Risk-Increasing Guard (`services/capital/capital_guard.py`):**
   - `CapitalGuard.authorize` validates inline `metadata.risk_policy` (or `risk_policy_ref`) on paper risk-increasing operations (`POST /api/capital-pools`, `PATCH /api/capital-pools/{pool_id}/status` with `status="active"`, `POST /api/bindings/{binding_id}/activate`, and risk-increasing `rebalance_apply`).
   - Inline paper validation executes in-memory with zero external reader calls (`_policy_loader`, `_safe_mode_reader`, `_approval_reader` remain uncalled when `risk_policy_ref` is null) and rejects missing, invalid, inactive, wrong-stage, negative-limit, stale, wrong-tenant, or unmapped required policies with `403 Forbidden` (`allow_new_or_increased_risk=False`).
3. **Risk-Reducing Actions Remain Available (`allow_risk_reducing_actions=True`):**
   - Suspending or archiving a pool (`PATCH /api/capital-pools/{pool_id}/status` with `status="suspended"` or `"archived"`), suspending or revoking a binding (`PATCH /api/bindings/{binding_id}/status` with `status="suspended"` or `"revoked"`), applying a risk-decreasing rebalance, and freezing containment never call `CapitalGuard.authorize` and remain available even when a pool's risk policy is missing, invalid, stale, or wrong-tenant.

### 1.3 Lifecycle Coverage: New Pools, Existing Pools, Retries, Rebinding, Renewal, and Active `RuntimeBinding` Projection

1. **Provisioning & Governed Repair (`services/control-plane/bff/persona_provisioning_coordinator.py`):**
   - `_coordinate_capital_pool` preserves legitimate explicit `risk_policy` / `risk_policy_ref` contracts on new pool creation, repairs missing or outdated policies on existing pools during retry/rebinding via `PATCH /api/capital-pools/{pool_id}/status`, and validates the effective policy on authoritative GET readback.
   - `publish_pool_envelope` verifies that the bound `CapitalPool`'s risk policy is resolved, active, paper-stage, and tenant-matched before creating or dispatching a replacement `DeploymentPlan`.
   - `repair_paper_pool_risk_policy` (exposed at `POST /bff/personas/{persona_id}/provisioning/paper-capital/risk-policy`, alongside `GET /api/capital-pools/{pool_id}/risk-policy`) provides idempotent governed repair and readback for dev paper pools. Once converged, subsequent calls return `idempotent_noop=True, repaired=False` with zero `PATCH` writes.
2. **Historical Registry Admission Expiry vs. Ongoing Renewal Semantics:**
   - `ApprovalDecision` records with `target_type="registry_entry"` carry a 24-hour candidate admission `expires_at` window consumed when `advance_artifact_state` transitions the candidate `RegistryEntry` to `approved`. That historical admission timestamp does not revoke an already-approved running strategy artifact.
   - Ongoing paper pool trading, rebinding, and pool-envelope renewal are governed by active `CapitalPool` status, active non-stale `RiskPolicy` (`status="active"`, matching version/digest, unexpired policy `expires_at`/`effective_to`), and `PersonaCapitalBinding` effective window (`effective_from`/`effective_to`).
3. **Active Replacement `RuntimeBinding` (`runtimeBindingId`) Projection:**
   - `services/control-plane/bff/personas/service.py::_resolve_active_runtime_binding_overlay` and `services/control-plane/bff/personas/routes/detail.py::bff_get_persona` resolve active replacement `RuntimeBinding`s (via `replacement_parent_binding_id` / `replaces_binding_id`) so `GET /bff/personas/{persona_id}` (`runtimeBindingId`) projects the active v2 pool-envelope binding instead of the retired v1 binding.
4. **Deployment & Runtime-Manager Verification (`services/deployment/service.py`, `services/runtime-manager/deploy_authority.py`, `services/runtime-manager/fleet_desired_state.py`):**
   - `DeploymentService.create_plan`, `verify_deploy_authorities`, and `build_fleet_desired_state` validate bound pool/plan/binding risk policies for tenant match, active status, target stage, non-staleness, and positive finite required limits.

## 2. Demonstration of Required Policy Resolution Cases

All cases below are captured in [evidence.json](evidence.json) under `case_demonstrations`:

| Case | Status | Reason Code | `allow_new_or_increased_risk` | `allow_risk_reducing_actions` | Summary |
|---|---|---|---|---|---|
| `missing` | `missing` | `risk_policy_missing` | `false` | `true` | Pool has neither `risk_policy_ref` nor `metadata.risk_policy`; no whole-pool fallback |
| `invalid` | `invalid` | `risk_policy_invalid_limit` / `risk_policy_invalid` | `false` | `true` | Empty `{}`, negative/non-positive limit (`gross_limit=-1.0`), inactive status, or non-paper stage |
| `stale` | `stale` | `risk_policy_stale` | `false` | `true` | Expired `expires_at`, `superseded_by`, or version/digest drift against expected/authoritative version |
| `wrong_tenant` | `wrong_tenant` | `risk_policy_wrong_tenant` | `false` | `true` | Policy or pool `tenant_id` (`tenant-foreign`) mismatches expected tenant (`tenant-dev`) |
| `valid` | `valid` | `null` | `true` | `true` | Active paper baseline policy (`gross_limit=2.0`, `net_limit=2.0`, `max_leverage=2.0`, `version="1.0"`) |
| `explicit_policy` | `valid` (`explicit_policy`) | `null` | `true` | `true` | Explicit policy contract (`rp-explicit-paper-custom` v2.1, `gross_limit=1.5`, `max_single_name_weight=0.25`) preserved |
| `unresolved_profile_mapping` | `unresolved_profile_mapping` | `risk_policy_profile_mapping_unapproved` | `false` | `true` | Surfaces `owner_decision_request` to `services/capital/risk_policy.py` without inventing percentages |

## 3. Three-Persona Dev Cohort Resolution and Probe Verification

Executed against the three enrolled dev paper personas from `PAPER-RISK-POLICY-PROVENANCE-20261121` (full output in [evidence.json](evidence.json)):

| Persona | Capital Pool ID | Policy ID & Version | Repaired `runtimeBindingId` (from stale v1 -> active v2) | Runtime `gross_limit` Budget (TWD) |
|---|---|---|---|---|
| 青禾 / `persona-2448c7053e33ea48da47` | `pool-persona-paper-06d4775a9b6a8f63b311` | `rp-pool-persona-paper-06d4775a9b6a8f63b311` @ `1.0` | `rb-3823397e125e4579ba5f4174067087ed` -> `rb-eb74118dd3ca4b169496b1c239087512` | `200000.0` (`2.0 * 100000.0`) |
| 澄川 / `persona-6507750312870d03a241` | `pool-persona-paper-039ad01f8bcd2b4aa466` | `rp-pool-persona-paper-039ad01f8bcd2b4aa466` @ `1.0` | `rb-7f3c1be55a224fdaa36bbdd7a7532b4b` -> `rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4` | `200000.0` (`2.0 * 100000.0`) |
| 衡岳 / `persona-ee7112a829d38b9135ac` | `pool-persona-paper-a81b4fd15944698341aa` | `rp-pool-persona-paper-a81b4fd15944698341aa` @ `1.0` | `rb-3516fc86e6f84649a499fc89f3c562b3` -> `rb-2b0df30559154a93902c209b0318d95c` | `200000.0` (`2.0 * 100000.0`) |

- **Probe Run A (`probe_without_owner_mapping_approval` in `evidence.json`):** `checked=3`, `effective_policy` resolved (`policy_errors == []`) and `persona_display_binding_id == binding_id` for all 3 personas; fails closed with `required_profile_mapping_unresolved` until an authoritative `capital_pool_activation` mapping decision is recorded by the risk policy owner.
- **Probe Run B (`probe_with_owner_mapping_approval` in `evidence.json`):** With the owner-approved `capital_pool_activation` decision bound to `pool.approval_digest` (which now includes `risk_policy_digest`), `passed=true, checked=3, failed=[]`.

## 4. Runtime Budget & Storage Manifest Registration

Registered in `docs/operations/storage-manifest.json` and validated with `python3 scripts/check_storage_manifest.py` (`passed: true, failures: []`):

- **`capital.capital_pools` & `/data/capital/capital_pools.json`**:
  - **Cadence:** 1 write at paper pool creation (`POST /api/capital-pools`); in-place keyed patch on status or risk-policy repair/rebinding (`PATCH /api/capital-pools/{pool_id}/status`); 0 writes/day in steady state once converged.
  - **Size & Count at Hosted Scale:** ~1.1 KB per pool record (including ~280 B `metadata.risk_policy` JSON object) across 3 enrolled dev paper pools (~3.5 KB total).
  - **Daily Growth:** 0 B/day steady-state (in-place keyed update by `pool_id`; idempotent repair short-circuits when `already_converged`).
  - **Cap & Cleanup:** 1 record per `pool_id` primary key (`single_runtime_enforced=True`); in-place overwrite of `metadata.risk_policy` clearing transient `risk_policy_stale`/`risk_policy_superseded_by`/`risk_policy_expires_at` markers; pool retirement marks `status="archived"`.
- **`capital.audit_events` & `capital_audit.jsonl`**:
  - **Cadence:** 1 `capital_pool_status_updated` event (~420 B) per actual pool status or risk-policy mutation; 0 events on idempotent repair replays (`idempotent_noop=True`).
  - **Size, Count & Daily Growth at Hosted Scale:** ~1.3 KB one-time across 3 dev paper pools on initial repair; 0 B/day steady-state once converged.
