# AG-GOV-WORKSHOP-CONTRACT-001 contract repair evidence

Status: **local implementation verified; ready for independent review**

This task repairs the two contract defects recorded by
`AG-HOSTED-CLOSE-001` evidence commit `ce7ba393e`:

1. Governance producers and the canonical ApprovalDecision schema now expose
   exactly one Workshop target type: `strategy_workshop`.
2. Workshop creation resolves `strategy_spec_ref` as a Registry identity,
   derives `strategy_id` from authoritative Registry readback, and persists
   the two identities separately.

## Enforced approval binding

Workshop research and conclude accept an approval only when all of these
authoritative fields match:

- `decision_state=decided`;
- `decision=approved` or `approved_with_conditions`;
- `tenant_id` equals the Workshop tenant;
- `owner_user_id` equals the Workshop owner;
- `target_type=strategy_workshop`;
- `target_id` equals the Workshop ID;
- `target_version` equals the selected Workshop version ID;
- the approval actor is present and differs from the Workshop requester.

The former noncanonical `workshop` target alias and missing owner/version
bindings are rejected.

## Public API regression

`test_public_exact_identity_approval_flow_survives_restart` uses public HTTP
routes for all three repaired surfaces:

- Registry:
  `POST /api/registry/strategy-specs` and authoritative GET readback;
- Workshop:
  public create, version list/create/select, consultation, research, conclude,
  and final GET readback;
- Governance:
  public propose, review, decide, and GET readback.

The fixture deliberately uses:

```text
strategy_id = strategy-public-workshop-contract
registry_id = registry-public-workshop-contract
```

It reconstructs the file-backed Governance `ApprovalDecisionStore` from disk
and creates a new BFF router before research and conclude. It then creates a
second new BFF router and reads the concluded Workshop again. The test asserts
that the BFF canonical approval projection preserves tenant, owner,
`strategy_workshop`, Workshop ID, and exact Workshop version.

Research and consultation authority remain narrow test adapters because this
task changes neither owner service. The flow asserts research-only,
`no_live_capital=true`, and performs no deployment or order operation.

## Verification

Run from the Pantheon repository root with the repository test environment:

```bash
/home/lupin/pantheon/.venv/bin/python -m py_compile \
  services/control-plane/governance/approval_decision.py \
  services/governance/models.py \
  services/control-plane/bff/agora/strategy_workshop/router.py \
  services/control-plane/governance/test_approval_decision.py \
  services/governance/test_governance_api.py \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py

/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/control-plane/governance/test_approval_decision.py \
  services/governance/test_governance_api.py \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  services/control-plane/bff/tests/test_agora_workshop_live_operations.py
```

Result on 2026-07-24: `174 passed, 5 skipped`.

The skips are pre-existing optional Postgres cases without
`TEST_DATABASE_URL`; the non-skipped restart regression uses the real
file-backed Governance store and reconstructed Workshop routers.

## Remaining publication gate

This evidence does not claim a hosted deployment. After independent review,
task PR merge, compatibility-gate regeneration if required, and exact FE/BFF
deployment, `AG-HOSTED-CLOSE-001` must rerun its hosted probe with distinct
Registry/strategy IDs and a real `strategy_workshop` approval.
