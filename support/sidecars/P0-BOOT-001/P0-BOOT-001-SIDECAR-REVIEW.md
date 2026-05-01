# P0-BOOT-001 Review Packet

**Sidecar Task:** P0-BOOT-001-SIDECAR-REVIEW  
**Parent Task:** P0-BOOT-001 — Materialize RuntimeBootstrapRequest from DeploymentPlan and RuntimeBinding  
**Prepared by:** Claude (sidecar owner)  
**Reviewer of this packet:** Codex  
**Intended for:** Codex2 (reviewer of P0-BOOT-001)  
**Prepared at:** 2026-05-01  
**Status of parent:** `review` — awaiting Codex2 review

---

## 1. Purpose

This packet supports Codex2's review of P0-BOOT-001 by providing:

- An evidence summary of what was implemented
- Acceptance criteria verification against the acceptance criteria in `ai-status.json`
- A mapping of each hard invariant (SD-P0-02 §8) to its test coverage
- Observations and follow-on gaps for the reviewer's consideration

This document is a support artifact only. It does not modify canonical truth.

---

## 2. Scope of P0-BOOT-001

**Goal:** Implement `materialize_runtime_bootstrap_request()` that transforms a `DeploymentPlan` + `RuntimeBinding` pair into a `RuntimeBootstrapRequest`, enforcing all safety gates for the P0 paper loop.

**Primary artifact:** `docs/04/pantheon_p0_sd/SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md`

**Files touched:**

| File | Role |
|---|---|
| `services/execution/lean_runtime/bootstrap_contract.py` | Core materializer + dataclasses |
| `services/execution/lean_runtime/test_bootstrap_contract.py` | Unit tests for the materializer |
| `services/execution/lean_runtime/test_runtime_identity.py` | Tests for RuntimeIdentity binding context |
| `services/execution/lean_runtime/test_paper_runtime.py` | Paper runtime service integration tests |

---

## 3. Acceptance Criteria Verification

From `ai-status.json` task record for P0-BOOT-001:

### AC-1: request includes deployment plan, runtime binding, artifact, capital, and bridge identity

**Result: PASS**

The materializer populates all required fields. Evidence from `test_materialize_bootstrap_request_from_deployment_plan`:

```python
payload["runtime_binding_id"]          == "rtb-paper-001"
payload["deployment_plan_id"]          == "dp-paper-001"
payload["deployment_stage"]            == "paper"
payload["runtime_role"]                == "paper"
payload["runtime_id"]                  == "rt-paper-001"
payload["artifact"]["artifact_id"]     == "art-alpha"
payload["artifact"]["artifact_version"] == "1.0.0"
payload["artifact"]["checksum"]        == "sha256:alpha"
payload["artifact"]["strategy_id"]     == "strat-alpha"
payload["capital"]["capital_pool_id"]  == "pool-paper-001"
payload["capital"]["persona_capital_binding_id"] == "pcb-paper-001"
payload["bridge"]["remote"]            == "ajoe734/pantheon-lean.git"
payload["bridge"]["source_path"]       == "pantheon/lean"
payload["bridge"]["commit"]            == "abc1234"
payload["runtime_config"]["paper_mode"] == True
payload["runtime_config"]["live_broker_enabled"] == False
```

`to_runtime_env()` propagates all identity fields as `PANTHEON_*` environment variables, verified in the same test.

### AC-2: request rejects secrets and lean-platform target

**Result: PASS**

Two dedicated tests cover this:

- `test_bootstrap_request_rejects_raw_secrets`: Rejects `broker_secret` and `api_key` keys with non-empty values anywhere in plan or binding.
- `test_bootstrap_request_rejects_lean_platform_target`: Rejects binding where `engine_bridge_repo` contains `lean-platform`.

Additional guard: `test_live_broker_activation_flag_is_rejected` — any plan that sets `live_broker_enabled: True` raises `BootstrapContractError`.

---

## 4. Hard Invariant Coverage (SD-P0-02 §8)

| Invariant | Description | Test Coverage |
|---|---|---|
| INV-BOOT-001 | runtime_bootstrap MUST NOT start live broker by default | `test_live_role_defaults_to_health_only`, `test_live_broker_activation_flag_is_rejected` |
| INV-BOOT-002 | target_stage=live MUST fail closed | `test_live_role_defaults_to_health_only` (health_only=True, live_broker_enabled=False) |
| INV-BOOT-003 | paper role may start without broker SDK | `test_materialize_bootstrap_request_from_deployment_plan` (paper_mode=True, health_only=False) |
| INV-BOOT-004 | live role starts health-only sidecar | `test_live_role_defaults_to_health_only` |
| INV-BOOT-005 | Request MUST reference RuntimeBinding | `test_bootstrap_request_requires_runtime_binding_id` |
| INV-BOOT-006 | Request MUST reference DeploymentPlan | `test_bootstrap_request_requires_deployment_plan_id` |
| INV-BOOT-007 | Request MUST include bridge path and commit | `test_materialize_bootstrap_request_from_deployment_plan` (bridge.commit, bridge.path verified) |
| INV-BOOT-008 | Target must point to pantheon/lean, not lean-platform | `test_bootstrap_request_rejects_lean_platform_target` |
| INV-BOOT-009 | Broker secret MUST NOT be included | `test_bootstrap_request_rejects_raw_secrets` |
| INV-BOOT-010 | bracket order remains logged_only | Not directly tested in this task — deferred to P0-LIVE-GUARD-001 |

---

## 5. Verification Evidence

```bash
# Run command verified by sidecar review
python3 -m pytest services/execution/lean_runtime/test_bootstrap_contract.py \
  services/execution/lean_runtime/test_runtime_identity.py \
  services/execution/lean_runtime/test_paper_runtime.py -v

# Result: 12 passed in 3.64s (verified 2026-05-01)
```

All 12 tests pass cleanly on the current branch (`backend-dev-publish-20260429`).

Breakdown:
- `test_bootstrap_contract.py`: 8 tests — all pass
- `test_runtime_identity.py`: 2 tests — all pass
- `test_paper_runtime.py`: 2 tests — all pass

---

## 6. Implementation Notes

### Materializer design

`materialize_runtime_bootstrap_request()` is intentionally pure: it accepts raw dicts, dataclasses, or objects with `to_dict()`. This allows the governance plane to serialize and pass the request through a manifest or environment without importing execution-plane modules.

### Stage/role normalization

The materializer accepts both current field names (`plan_id`, `binding_id`, `deployment_mode`) and SD-P0-02 aliases (`deployment_plan_id`, `runtime_binding_id`, `deployment_stage`). Cross-referencing ensures plan and binding agree on `deployment_stage`, `artifact_id`, `artifact_version`, and `capital_pool_id`.

### Bridge identity enforcement

The bridge remote is pinned to `ajoe734/pantheon-lean.git` and bridge source path to `pantheon/lean`. Any deviation raises `BootstrapContractError`. This implements INV-BOOT-008 at the materializer level.

### Secret rejection logic

`_reject_raw_secrets()` walks the full plan and binding recursively. Keys matching `_SECRET_KEY_MARKERS` (e.g., `secret`, `api_key`, `token`, `password`) are rejected if they have a non-empty value. Keys in `_SECRET_REFERENCE_KEYS` or ending in `_ref`, `_id`, `_path`, etc. are exempt, preserving valid reference fields like `auth_profile_ref` and `required_secret_keys`.

### to_runtime_env()

Produces `PANTHEON_*` environment variables for all identity fields without exposing secrets. The `PANTHEON_LIVE_BROKER_ENABLED` and `PANTHEON_HEALTH_ONLY` variables are always propagated, ensuring the bootstrap entrypoint can make role decisions without re-parsing the request.

---

## 7. Gaps and Follow-on Items

| Gap | Severity | Owner | Notes |
|---|---|---|---|
| INV-BOOT-010 bracket_order=logged_only not tested in this task | Low | P0-LIVE-GUARD-001 | Explicitly deferred per SD-P0-02 §13 |
| No integration test with actual `runtime_bootstrap.py` entrypoint | Low | P0-CTX-002 | This task is materializer-only; wiring is deferred |
| `RuntimeBootstrapResult` dataclass is defined in SD-P0-02 §5.4 but not yet implemented | Low | Future task | Result shape not needed until runtime wiring |
| `to_runtime_env()` does not include `PANTHEON_APPROVAL_DECISION_ID` when absent | Observation | None | Correct behavior; approval is optional metadata |
| Tests use `deployment_mode` (current field name) vs SD alias `deployment_stage` | Observation | None | Both paths are supported by the materializer |

---

## 8. Reviewer Checklist for Codex2

For your review of P0-BOOT-001, please verify:

- [ ] `bootstrap_contract.py` does not import from governance or registry services (pure materializer requirement)
- [ ] All hard invariants in SD-P0-02 §8 are covered by at least one test (see §4 above — INV-BOOT-010 is deliberately deferred)
- [ ] The two task-level acceptance criteria in `ai-status.json` are both satisfied (see §3 above)
- [ ] No canonical L1 policy documents were modified by this task (confirmed: no L1 file changes)
- [ ] The `to_runtime_env()` output does not expose broker credentials or raw secrets
- [ ] Stage cross-validation (plan vs binding) prevents inconsistent deployment stage from propagating

---

## 9. Handoff Note

This packet is ready for Codex2's review of P0-BOOT-001. No canonical truth was modified in preparing this packet.

**Dependent tasks unlocked after P0-BOOT-001 is approved:**

- P0-CTX-001 (depends_on: P0-BOOT-001) — PantheonRuntimeContext model
- P0-LIVE-GUARD-001 (depends_on: P0-BOOT-001) — live fail-closed and bracket logged-only honesty

---

*Prepared by Claude as sidecar support for P0-BOOT-001 review. See `ai-status.json` task `P0-BOOT-001-SIDECAR-REVIEW` for lifecycle state.*

---

## 10. Closeout Record

**Reviewer approval (Codex):** Approved 2026-05-01. Packet stays within sidecar scope; does not modify canonical truth. Residual INV-BOOT-010 gap intentionally parent-owned (P0-LIVE-GUARD-001).

**Parent P0-BOOT-001 review (Claude):** Approved 2026-05-01. AC-1 and AC-2 pass; INV-BOOT-001 through INV-BOOT-009 verified; 12 tests pass. Returned to Codex for finalization.

**Task status:** done
