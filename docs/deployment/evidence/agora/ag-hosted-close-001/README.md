# AG-HOSTED-CLOSE-001 hosted qualification

Status: **blocked — do not approve closeout**

Qualified on 2026-07-24 against replacement project
`pantheon-lupin-dev-20260719`, VM `pantheon-lupin-dev`, external IP
`35.201.204.12`.

## Exact hosted identity

- Frontend: `e4399e3ec68f882ace35d0349e6597cdd101525f`
- BFF: `00b38f41ec51296762d502c4bd5732f95ccf2953`
- Pair ID:
  `5b5d84cb24e4f7280a02924591d01f570f3d73d791f5761c98dc67a571e9a55f`
- Compatibility manifest SHA-256:
  `494980f204f0af21effc018ebbba657c1027b3052e984577833dfa46ab360bb3`
- Contract: `agora.v1.13`
- Frontend posture: `read-only`, live BFF, strict fallback, real/stub writes
  disabled, no embedded bearer token.
- BFF posture: strict auth, auth stub disabled, dev login enabled, MFA
  required.

The first deployment observed during this task, run `30059251735`, switched the
BFF to `be3cf913091279fc893846244c422cd50609505e` while the accepted frontend
manifest still named `00b38f41...`. That drift was rejected as evidence.
Governed dev workflow run `30060196528` restored the exact accepted BFF and
passed its public version check and built-in Agora restart-persistence smoke.
Public `/healthz` then reported `status=ok`, `ready=true`, and zero lifecycle
projection backlog.

## Qualified product behavior

The task-owned probe used four separate server-bound identities. Viewer,
operator A, operator B, and approver JWTs all carried `mfa_verified=true`; no
credential or token was emitted. The probe used Registry, Governance, and BFF
HTTP APIs only. It did not edit a store or intercept a route.

Run `ag-hosted-close-20260724T020356Z-c15ae7` proved:

- anonymous Agora read was denied with `401`;
- viewer workshop write was denied with `403`;
- operator B cross-owner workshop read was denied with
  `403 CROSS_USER_ACCESS_FORBIDDEN`;
- Registry StrategySpec create, workshop create/read, version list/create,
  version select, and committee consultation returned their implemented typed
  success responses;
- a distinct approver completed Governance review and decision for the closest
  supported approval type.

The structured result is
[`qualification-20260724T020356Z.json`](qualification-20260724T020356Z.json).
The reusable probe is
[`hosted_workshop_probe.py`](hosted_workshop_probe.py).

## Blocking contract defects

### 1. No producer can create the approval required by Workshop

`POST /api/governance/approvals` with the Workshop-required
`target_type=strategy_workshop` returns `422`. The canonical `TargetType` enum
only accepts `registry_entry`, `strategy_spec`, `model_artifact`,
`allocation_policy`, `persona_capital_binding`, or `evolution_proposal`.

A real `strategy_spec` approval was created, reviewed, and approved by the
distinct approver. Both downstream Workshop operations then failed closed:

- research run: `409 HUMAN_GATE_PENDING`,
  reason `APPROVAL_TARGET_TYPE_MISMATCH`;
- conclude: `409 HUMAN_GATE_PENDING`,
  reason `APPROVAL_TARGET_TYPE_MISMATCH`.

The router explicitly requires approval target type `strategy_workshop` or
`workshop`, so the producer and consumer contracts have an empty intersection.
No permitted product API can satisfy the human gate.

### 2. Normal Registry and Workshop identities disagree

Run `ag-hosted-close-20260724T020155Z-1dee60` used a normal distinct
`strategy_id` and `registry_id`. Workshop creation succeeded, but version list
and version create both returned:

```text
409 RESOURCE_CONFLICT
STRATEGY_SPEC_STRATEGY_ID_MISMATCH
precondition_failed=strategy_version_projection
```

The deployed create route stores `strategy_spec_ref` in both the session
`strategy_id` and `active_strategy_spec_registry_id`. The final probe used one
valid shared identifier only to continue to the independent approval blocker.
That is not proof that the general distinct-ID path works.

## Acceptance impact

AG-HOSTED-CLOSE-001 cannot be marked done:

- two of the six formerly deferred operations do not return implemented
  success behavior on the accepted hosted pair;
- there is no conclusion to compare across restart;
- downstream candidate, Trading Room, dashboard, Performance receipt, bounded
  source refresh, and final desktop/mobile closeout cannot constitute an
  end-to-end accepted chain while the mandatory Workshop human gate is
  unreachable.

Existing workflow restart smoke remains valid partial infrastructure evidence,
but it cannot replace this missing product-level chain.

## Required repair and requalification

1. Add one canonical Workshop approval target to the Governance/Promotion
   producer contract and preserve it through the BFF canonical read projection.
2. Align Workshop create semantics so session `strategy_id` comes from the
   Registry entry while `active_strategy_spec_registry_id` remains the Registry
   identity.
3. Add integration coverage that begins with public Workshop create and a real
   approval producer; in-memory fake approvals are insufficient.
4. Regenerate the Agora contract/handoff as needed, pass the compatibility
   gate, deploy a new exact FE/BFF pair, then rerun this probe and the remaining
   acceptance matrix.

Rollback result: the qualification made no execution/capital change, and the
frontend stayed in the accepted read-only profile. The task qualification
lease was released at `2026-07-24T02:04:21Z`.
