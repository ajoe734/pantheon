# PPL-ALLOC-009 BFF and Frontend Handoff Packet

**Sidecar Task ID**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF`  
**Parent Task**: `PPL-ALLOC-009`  
**Parent Owner**: `Antigravity`  
**Parent Reviewer**: `Claude`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-12  
**Mutates Canonical**: `no`

This packet is support material only. It does not change L1 truth, service
contracts, BFF or frontend runtime code, registry/governance behavior, or
deployment configuration. The parent owner decides whether to absorb its
queries, journey, and evidence checklist into the production closeout.

## 1. Parent Closeout Target

`PPL-ALLOC-009` must close the full operator path with merged PRs, deployed
SHAs, local validation, hosted frontend/BFF smoke, and named residual risks:

```text
create persona -> paper_running bundle
paper ranking -> promotion review -> human decision
real ranking -> target weights -> rebalance proposal -> human approval -> apply receipt
emergency breach -> containment without promotion or allocation increase
```

The primary operator route is `/management/promotion-allocation`. Persona
Fleet, Human Inbox, Capital, and rebalance detail are supporting surfaces;
ranking/readiness pages are diagnostic only.

## 2. Current BFF Surface Snapshot

The following are code-level facts, not hosted-dev proof:

| Capability | Current BFF surface | Closeout use |
| --- | --- | --- |
| Atomic paper creation | `POST /bff/management/personas/create-paper-bundle` | Capture persona, isolated `paper_ledger_id`, runtime binding, deployment plan, paper stage, and no-live-side-effect metadata. |
| Fleet/binding identity | Persona Fleet projections expose paper ledger, runtime binding, pool/sleeve, current/target weight, and next-action fields. | Prove paper identity is not presented as a shared real pool and that canary/live bindings remain distinguishable. |
| Promotion recommendation | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Capture the accepted review reference and target stage; submission must not claim capital mutation. |
| Promotion review | `GET /bff/management/promotion-reviews`, `GET /{review_id}`, `POST /{review_id}/decisions` | Trace recommendation through Human Inbox decision and receipt. |
| Allocation evaluation | `POST /bff/management/allocation-policy/evaluate` | Produce stage-aware target lines with `applied: false`; this is calculation, not authorization or apply. |
| Rebalance proposal | `POST /bff/rebalances`, `GET /bff/rebalances/{id}` | Persist ranking snapshot, lines, simulation, constraints, rollback target, approval/audit refs. |
| Rebalance apply authorization | `POST /bff/rebalances/{id}/apply` | Verify live increases fail without `approval_ref`; capture command receipt when admitted. |
| Emergency guard | Emergency containment command validation and emergency rebalance-line validation | Prove risk-decreasing actions are admitted while promotion/increase attempts fail closed. |

## 3. BFF Query Gap Matrix

| Closeout question | Available evidence path | Gap the parent must resolve or record |
| --- | --- | --- |
| Can one query reconstruct the full persona-to-allocation journey? | Individual persona/fleet, promotion-review, rebalance, and command surfaces. | No single closeout aggregation endpoint is established here. The smoke harness must retain correlation IDs and join `persona_id`, `review_id`, `ranking_snapshot_id`, `rebalance_id`, `command_id`, and audit refs without guessing. A future aggregate is optional; do not delay closeout if deterministic joins are proven. |
| Which ranking snapshot produced a review and proposal? | Promotion and rebalance payloads carry recommendation/review and `ranking_snapshot_id` data. | Hosted evidence must show stable identifiers across the actual responses. Missing linkage is a residual risk, not something the frontend may synthesize. |
| Has an accepted command actually applied? | Apply returns a command receipt; command status is the execution truth. | A `202` or proposal `applied: false` is not completion. Parent must poll the owning command/status surface and record terminal state plus audit receipt. |
| Is approval authoritative for a live increase? | Apply rejects a live increase without `approval_ref`. | Smoke must include both the negative probe and the approved path. A UI-enabled button or decision record alone is insufficient. |
| Can containment increase/promote? | BFF validators reject emergency increases and promotion semantics. | Run explicit negative hosted/API probes and preserve sanitized `4xx` evidence; do not exercise an unsafe positive live mutation. |
| Are reads current enough for operator decisions? | Responses expose snapshot/meta fields on relevant read surfaces. | Record `snapshot_at`, staleness warnings, and deployed SHA. If stale/fallback data appears, label the proof degraded and assign a residual-risk owner. |
| Can the frontend distinguish paper, canary, and live capital? | Fleet/capital projections expose ledger/pool/sleeve identities and weights. | Hosted browser evidence must show labels and null/unknown handling. Never coerce missing weights to zero or a paper ledger into a real pool. |

## 4. Operator Journey And Evidence Capture

### A. Create and inspect a paper persona

1. From `/management/personas`, submit Create Paper Persona with a unique
   idempotency key.
2. Record the response and confirm `paper_running`, isolated
   `paper_ledger_id`, runtime binding, deployment plan, data/risk settings, and
   `live_capital_side_effects: false`.
3. Open Persona Fleet and Capital. Capture the same persona/ledger identity and
   verify that no real pool or sleeve is implied.
4. If any creation step fails, the UI must route to setup repair with the exact
   failed step; it must not present an inert shell as running.

### B. Paper-to-real review

1. Open `/management/promotion-allocation?tab=paper-candidates` and select the
   created or seeded eligible paper persona.
2. Submit the recommendation; retain recommendation and review IDs, target
   stage, evidence refs, idempotency/correlation IDs, and response time.
3. Open the matching Human Inbox detail and make the authorized human decision.
4. Confirm a decision receipt exists and that neither submit nor approval
   claims a direct full-live capital mutation.

### C. Quarterly real allocation

1. Open the real-ranking tab and capture the ranking snapshot, eligibility,
   exclusion reasons, caps, and evidence confidence.
2. Evaluate target allocations; verify the response says `applied: false`.
3. Create a rebalance proposal with current/target/delta, pool or sleeve,
   simulation, constraints, rollback target, cap reasons, and evidence refs.
4. Negative probe: attempt a live increase without `approval_ref`; expect a
   fail-closed response.
5. After authorized approval, apply once with a stable idempotency key, retain
   the command ID, poll terminal command status, and capture the audit receipt.
6. Refresh Capital and rebalance detail from BFF truth instead of applying
   optimistic local weights.

### D. Emergency containment

1. Use a safe seeded/dev persona with an accepted hard-risk trigger and
   evidence refs.
2. Submit a reduce/freeze/suspend containment action and capture its receipt.
3. Negative probes must reject `target_weight > current_weight` and any
   promotion target.
4. Refresh Promotion & Allocation and Capital; show the containment state and
   ensure no promotion/increase side effect appears.

## 5. Frontend Handoff Contract

- Build and host `execute-plans` from its own repository with
  `VITE_BFF_MODE=live`, the Pantheon dev BFF URL, and
  `VITE_BFF_FALLBACK=strict`.
- Browser calls go to the BFF only. Do not call internal services or turn fixture
  data into formal closeout evidence.
- Preserve server identifiers and idempotency keys across retries. A timeout
  after a write requires lookup/polling, not a fresh mutation with new IDs.
- Render `loading`, `empty`, `stale/degraded`, `forbidden`, `validation error`,
  `conflict/precondition`, and `terminal command failure` distinctly.
- Treat `401` as session/auth recovery, `403` as authority denial, `409` as a
  precondition/idempotency conflict, and `422` as input repair. Do not relabel
  any of them as an empty ranking or successful no-op.
- Missing `paper_ledger_id`, binding identity, stage, evidence, cap reason, or
  approval reference must be visible as incomplete/blocked. Do not manufacture
  labels, zero weights, eligibility, or next actions client-side.
- Legacy routes should redirect to the matching workbench tab. Diagnostic
  ranking/readiness pages must link to, not impersonate, the action workbench.

## 6. Parent Closeout Evidence Ledger

The parent archive should provide one row per proof step:

| Field | Required content |
| --- | --- |
| Build identity | Pantheon PR/merge SHA, Execute Plans PR/merge SHA, deployed BFF SHA, deployed frontend SHA/bundle identity. |
| Environment | Hosted FE and BFF origins, timestamp, auth role, strict fallback posture; redact tokens and secrets. |
| Request identity | Route/action, idempotency key, correlation/trace ID, safe fixture/persona ID. |
| Domain linkage | Persona, ledger/binding, recommendation/review, ranking snapshot, proposal/rebalance, command, approval/audit IDs as applicable. |
| Result | HTTP status, salient response fields, terminal command state, and UI screenshot or browser assertion. |
| Safety claim | Explicitly state whether the step is read-only, proposal-only, approved apply, or risk-decreasing containment. |
| Residual risk | Concrete gap, severity, owner, expiry/recheck condition. Use `none observed` only after the relevant negative probe. |

Minimum negative probes:

- paper creation request carrying real/live capital intent is rejected;
- promotion submission cannot request direct full-live mutation;
- live allocation increase without human approval is rejected;
- emergency containment cannot promote or increase allocation;
- strict frontend mode does not silently fall back to fixture/mock success.

## 7. Recommended Verification

Focused local BFF contract suite:

```bash
python3 -m pytest -q \
  services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py \
  services/control-plane/bff/tests/test_bff_capital_pool_bindings.py \
  services/control-plane/bff/tests/test_bff_persona_allocation_policy.py \
  services/control-plane/bff/tests/test_bff_rebalance_proposals.py \
  services/control-plane/bff/tests/test_bff_emergency_containment.py
```

Packet hygiene:

```bash
git diff --check -- \
  support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md
```

These local checks establish contract behavior only. They do not replace the
parent's hosted smoke, deployed-SHA confirmation, or browser evidence.

## 8. Reviewer And Parent-Owner Checklist

- [ ] Packet remains support-only and does not redefine canonical semantics.
- [ ] Implemented BFF facts are not presented as hosted or production proof.
- [ ] Correlation across persona, review, ranking, rebalance, command, and audit
      identities is deterministic in the evidence ledger.
- [ ] A `202`, recommendation, decision, or proposal is never called applied
      capital without terminal command/receipt evidence.
- [ ] Negative probes prove approval and emergency fail-closed boundaries.
- [ ] Frontend evidence uses live/strict BFF mode and distinguishes degraded or
      missing truth from successful empty state.
- [ ] Parent closeout records both repository PRs/merge SHAs, deployed commits,
      validation, hosted smoke, and residual-risk owner/expiry.

## 9. Handoff Boundary

This sidecar gives `Antigravity` a closeout query plan, operator journey,
frontend behavior contract, and evidence-ledger template. It intentionally
does not implement an aggregate endpoint, change frontend code, deploy either
repository, approve capital operations, or declare `PPL-ALLOC-009` complete.
Parent ownership and canonical acceptance remain with the parent task.
