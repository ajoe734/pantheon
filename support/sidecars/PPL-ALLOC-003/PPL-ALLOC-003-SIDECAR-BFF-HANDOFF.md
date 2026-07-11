# PPL-ALLOC-003 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `PPL-ALLOC-003` — Capital binding read model  
**Parent Owner**: Gemini2  
**Parent Reviewer**: Claude  
**Sidecar Task**: `PPL-ALLOC-003-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: Codex2  
**Sidecar Reviewer**: Codex  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11

> Support artifact only. This packet does not change canonical truth or the
> runtime, registry, governance, BFF, or frontend implementation. The parent
> owner decides which recommendations to compose into `PPL-ALLOC-003`.

## 1. Handoff Summary

The current persona-fleet projection already separates an isolated paper
ledger from real capital identity: paper rows expose `paper_ledger_id`, clear
`capital_pool_id`, and preserve the old pool value only as
`legacy_paper_capital_pool_id`. The remaining parent-task gap is to normalize
canary/live binding identity and allocation weights and to compose the same
truth into capital pool/sleeve rows.

The frontend should consume one normalized binding summary rather than infer
capital identity from deployment stage, pool names, or legacy paper pool ids.
No read DTO may imply that a target weight has been approved or applied.

## 2. Existing BFF Surfaces and Gaps

| Surface | Existing useful behavior | Gap for `PPL-ALLOC-003` |
|---|---|---|
| `GET /bff/management/persona-fleet` | Composes persona, league, persona binding, runtime binding, telemetry, incidents, and review state. Paper rows expose isolated ledger identity and migration trace separately. | Rows do not consistently expose `capital_scope`, `capital_sleeve_id`, `current_weight`, `target_weight`, or `binding_state`. The first binding selected for a persona can hide multiple sleeve/binding records. |
| `GET /bff/capital-pools` | Lists canonical pool records with pagination and surface metadata. | Returns raw pool rows without normalized persona/sleeve binding summaries, so the capital view cannot reliably deep-link back to the persona or distinguish sibling sleeves. |
| `GET /bff/capital-pools/{pool_id}` | Adds raw `bindings` to pool detail and reports binding-source degradation. | Raw bindings do not provide a stable cross-stage summary or distinguish current versus proposed target allocation. |
| Persona catalog and persona-health reads | Already clear `capital_pool_id` for paper personas and expose `paper_ledger_id`. | They should remain compatibility/health surfaces; do not make the frontend join them to reconstruct allocation truth. |

Relevant implementation seams are
`_project_persona_fleet_list_row`, `_persona_fleet_slim_list_payload`,
`bff_list_capital_pools`, and `bff_get_capital_pool` in
`services/control-plane/bff/main.py`.

## 3. Recommended Normalized Binding Summary

Use the same snake_case summary in persona-fleet and capital pool/sleeve
projections. Compatibility aliases may remain where already established, but
new frontend work should use the snake_case fields.

```json
{
  "stage": "paper | canary | live | none",
  "paper_ledger_id": "paper-ledger-persona-123",
  "runtime_binding_id": "runtime-binding-123",
  "capital_scope": "paper_ledger | canary_sleeve | live_sleeve | live_pool | unbound",
  "capital_pool_id": "pool-real-1",
  "capital_sleeve_id": "sleeve-persona-123",
  "current_weight": 0.12,
  "target_weight": 0.15,
  "binding_state": "active | pending | missing | degraded | retired",
  "legacy_paper_capital_pool_id": null
}
```

Normalization rules:

- Paper: require a persona-isolated `paper_ledger_id`; set real pool, sleeve,
  current weight, and target weight to `null`; set `capital_scope` to
  `paper_ledger`. A legacy paper pool id is migration trace only.
- Canary: require a real parent pool plus a distinct sleeve when the pool is
  shared; expose the applied allocation as `current_weight`.
- Live: require a real sleeve or an explicit pool-level allocation identity;
  shared parent pools must remain distinguishable by sleeve or weight.
- `target_weight` is proposed policy output. Its presence never means applied.
  The allocation/rebalance task owns recommendation, approval, and apply
  semantics.
- Missing required identity produces `binding_state: "missing"`; unavailable
  or stale backing surfaces produce `"degraded"`. Do not synthesize a real
  pool or sleeve from a display label.
- If several bindings exist, select by active validity and stage, or return a
  collection. Do not silently use the first record without exposing ambiguity.

## 4. Composition Boundary

`PPL-ALLOC-003` owns read-model normalization only. It should compose with:

- `PPL-ALLOC-002` for the paper ledger and runtime binding created by the
  create-paper bundle;
- `PPL-ALLOC-004` for target weights, cap reasons, rebalance proposal state,
  approval, and apply receipts;
- `PPL-ALLOC-005` for create-paper success/repair presentation;
- `PPL-ALLOC-006` and `PPL-ALLOC-007` for workbench rows, drill-downs, and
  route pruning.

It must not create allocation policy, mutate weights, infer human approval,
change runtime binding authority, or promote a persona.

## 5. Frontend Operator Journey

1. Persona Fleet shows stage and one unambiguous capital identity. A paper row
   links by paper ledger; canary/live rows link by sleeve first and parent pool
   second.
2. Promotion & Allocation reads `current_weight` as applied truth and
   `target_weight` as proposed truth. It shows a delta only when both values
   are present.
3. The row links to rebalance review/detail for approval state; it never labels
   target weight as applied based only on the binding DTO.
4. Capital pool detail lists persona binding summaries, keeping sibling
   sleeves distinct even when they share a parent portfolio pool.
5. Missing/degraded binding state routes the operator to repair/readiness, not
   to an allocation-increase action.

Suggested link fields on each summary are `persona`, `paper_ledger`,
`capital_sleeve`, `capital_pool`, `runtime_binding`, and `rebalance_detail`.
Null links should be explicit when their identity is not applicable.

## 6. Parent Acceptance and Test Matrix

Add or extend contract coverage for these cases:

| Case | Required assertions |
|---|---|
| Isolated paper | Unique `paper_ledger_id`; `capital_scope=paper_ledger`; real pool/sleeve/weights null; legacy id trace-only. |
| Canary sleeve | Parent pool and sleeve present; applied current weight present; stage/scope/binding state consistent. |
| Live sleeve | Real identity and current weight present; target remains proposal truth and is not marked applied. |
| Shared parent pool | Two personas retain distinct sleeves or explicit allocation identities; no row collapses into the parent pool. |
| Missing binding | `binding_state=missing`; no fabricated real identity; repair/readiness link available. |
| Degraded source | Stable DTO with degraded metadata/state; no 500 and no false active binding. |
| Legacy migration | Old paper pool retained only in `legacy_paper_capital_pool_id`; never copied into primary `capital_pool_id`. |
| Pool composition | Pool list/detail binding summaries deep-link to persona and sleeve/ledger and agree with persona-fleet fields. |

Focused parent validation:

```sh
python3 -m pytest services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py -q
python3 -m pytest services/control-plane/bff/tests/test_bff_capital_pool_bindings.py -q
git diff --check
```

The second test file is a parent-task target and may not exist until the parent
implementation creates it. This sidecar does not add runtime tests or code.

## 7. Reviewer Checklist

- Paper capital identity remains ledger-first and isolated.
- Canary/live identity is not reconstructed from legacy paper pool data.
- Current, target, approval, and applied states remain semantically distinct.
- Pool and persona projections agree on identifiers and weights.
- Missing/degraded bindings fail closed and remain operator-visible.
- Changes stay within the parent BFF/read-model scope and compose cleanly with
  the allocation-policy and frontend owners.
