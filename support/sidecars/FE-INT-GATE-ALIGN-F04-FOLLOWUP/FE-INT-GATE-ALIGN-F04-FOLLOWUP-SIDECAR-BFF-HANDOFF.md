# FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF

- Task: `FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF`
- Parent task: `FE-INT-GATE-ALIGN-F04-FOLLOWUP`
- Helper kind: `bff_handoff_packet`
- Owner: Codex
- Reviewer: Claude
- Scope: support artifact only

## Boundary

This packet is a support-sidecar record for parent-owner absorption. It does
not change L1 canonical truth, BFF runtime implementation, registry behavior,
governance policy, or execute-plans frontend code.

The sidecar output is this file only. It should be used as context for future
BFF/frontend coordination around the already-closed F04/F04-FOLLOWUP work.

## Parent State

- `FE-INT-GATE-ALIGN-F04` is archived as `done`. It aligned
  `e2e/04b-optimization-loop.spec.ts` to the hosted Lovable DOM and kept a
  temporary fallback to generic Approvals/Interventions shell navigation while
  the hosted bundle lacked row-level approval/HIQ controls.
- `FE-INT-GATE-ALIGN-F04-FOLLOWUP` is archived as `done`. It restored row-level
  optimization approval control in execute-plans commit
  `8c7606cf6904e63eb265427cef25f8d226e10cbf`, then recorded Pantheon closeout
  in commit `e9a4ad1159feb5f955f8a28af228f702e389ce52`.
- Parent acceptance is already reviewed: the optimization awaiting-approval row
  now exposes row-scoped Approvals/HIQ navigation and the F04 spec no longer
  accepts generic shell-nav fallback.

## BFF Query-Gap Conclusion

No new backend BFF route gap was found for the F04/F04-FOLLOWUP acceptance
surface.

Current BFF facts:

- The route manifest marks `GET /bff/v5/loop-runs` and
  `GET /bff/v5/loop-runs/{param}` as implemented
  (`services/control-plane/bff/contract_snapshots/backend_routes_manifest.json:1153`).
- The BFF read alias serves the loop list and detail surfaces from
  `read_store.list_loop_runs()` / `read_store.get_loop_run()`
  (`services/control-plane/bff/main.py:23437`,
  `services/control-plane/bff/main.py:23617`,
  `services/control-plane/bff/main.py:23670`).
- The read-store source can derive loop runs from incidents or use a dedicated
  `loop_runs` store (`services/control-plane/bff/read_store.py:1219`,
  `services/control-plane/bff/read_store.py:1243`).
- Focused BFF contract tests cover list, detail, unknown ID, and missing-source
  behavior for `/bff/v5/loop-runs`
  (`services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py:68`).

The gap fixed by the parent was frontend adapter/rendering behavior, not a
missing backend route. The execute-plans frontend already requests
`/bff/v5/loop-runs` through `paths.v5LoopRuns()`
(`/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts:132`) and then filters
client-side by `loopKind` when `v5.loops.list("optimization")` is called
(`/home/lupin/code/execute-plans/src/lib/bff/v5.ts:586`). The backend does not
need a new `GET /bff/v5/optimization-loop` or server-side `kind=optimization`
query for this row-level journey to pass.

What BFF should preserve when real runtime loop data is promoted:

- Keep `GET /bff/v5/loop-runs` returning list envelopes with strict `items`.
- For optimization loop rows that await human action, populate at least one of:
  - `nextAction` or `next_action` with `kind` containing approval, `label`, and
    optional management `href`;
  - approval evidence in `evidence` or `evidence_refs`;
  - an `approval` object with `approval_id`, `id`, or management links;
  - an approval stage in `stages` or `timeline` with `entity_type=approval`,
    `entity_id`, and optional `action_href`.
- If HIQ routing is available, preserve management links such as
  `/management/interventions?intervention=<hiq_id>` alongside the approval link.

This is a data-population/preservation note, not a request for a new BFF
endpoint or canonical contract change.

## Operator Journey

Target surface: `/management/loops/optimization`.

1. Operator opens `/management/loops/optimization` while authenticated against
   the BFF-backed management UI.
2. The page loads `v5.loops.list("optimization")`, which requests
   `GET /bff/v5/loop-runs` and adapts each live DTO into a `LoopRun`.
3. Operator locates the row for the rebalance subject. The expected C01 journey
   shows ranking -> rebalance -> approval -> apply -> evolution promotion, with
   the approval/apply portion blocked or awaiting approval.
4. In that same table row, the Next column should show a row-level link such as
   `Review approval`. The link should route to
   `/management/approvals?approval=<approval_id>` when approval evidence is
   present.
5. The Evidence column should show the approval id as a row-level link to the
   same approval surface.
6. If the loop DTO exposes an HIQ/intervention link, the row-level action may
   route to `/management/interventions?intervention=<hiq_id>` instead of, or in
   addition to, the approval queue.
7. Generic shell navigation to `/management/approvals` or
   `/management/interventions` outside the rebalance row is no longer sufficient
   for F04/F04-FOLLOWUP acceptance.

This journey is read/navigation handoff only. It does not define or authorize a
new approval command path, live-capital activation, or governance mutation.

## Frontend Handoff

Primary execute-plans artifacts from parent commit
`8c7606cf6904e63eb265427cef25f8d226e10cbf`:

- `/home/lupin/code/execute-plans/src/lib/v5/types.ts:37` adds optional
  `LoopRunNextAction.href`.
- `/home/lupin/code/execute-plans/src/lib/bff/v5.ts:72` accepts only
  `/management/...` next-action hrefs.
- `/home/lupin/code/execute-plans/src/lib/bff/v5.ts:158` maps backend
  `nextAction` / `next_action` approval kinds and labels.
- `/home/lupin/code/execute-plans/src/lib/bff/v5.ts:213` maps approval evidence
  from `evidence`, `evidence_refs`, `evidenceRefs`, and nested `approval`.
- `/home/lupin/code/execute-plans/src/lib/bff/v5.ts:249` adapts a BFF loop run,
  including `timeline`/`stages`, approval-stage detection, approval href
  fallback, and `loopFamily`/`loop_family` optimization detection.
- `/home/lupin/code/execute-plans/src/management/pages/v5/OptimizationLoop.tsx:41`
  loads optimization loops through `v5.loops.list("optimization")`.
- `/home/lupin/code/execute-plans/src/management/pages/v5/OptimizationLoop.tsx:95`
  derives row-local approval evidence and next-action link state.
- `/home/lupin/code/execute-plans/src/management/pages/v5/OptimizationLoop.tsx:124`
  renders the row-level next-action link.
- `/home/lupin/code/execute-plans/src/management/pages/v5/OptimizationLoop.tsx:130`
  renders the approval evidence id as a row-level approval link.
- `/home/lupin/code/execute-plans/e2e/04b-optimization-loop.spec.ts:130`
  defines the canonical C01 stage fixture used by the F04 test.
- `/home/lupin/code/execute-plans/e2e/04b-optimization-loop.spec.ts:322`
  defines the optimization loop DTO with approval next action, approval
  evidence, approval object, and management links.
- `/home/lupin/code/execute-plans/e2e/04b-optimization-loop.spec.ts:743`
  verifies that awaiting approval links to Approvals/HIQ from inside the
  rebalance row, with the generic shell-nav fallback removed.

Expected minimal DTO shape for this journey:

```json
{
  "id": "loop-run-c01-optimization",
  "loop_family": "optimization",
  "status": "blocked",
  "subject_kind": "rebalance",
  "subject_id": "rebalance-c01-paper",
  "next_action": {
    "kind": "awaiting_approval",
    "label": "Review approval",
    "href": "/management/approvals?approval=approval-c01-rebalance"
  },
  "stages": [
    {
      "stage": "approval",
      "kind": "awaiting_approval",
      "status": "blocked",
      "entity_type": "approval",
      "entity_id": "approval-c01-rebalance",
      "action_href": "/management/approvals?approval=approval-c01-rebalance"
    }
  ],
  "evidence": [
    { "kind": "approval", "id": "approval-c01-rebalance" }
  ]
}
```

The frontend can also consume the camelCase variants already handled by the
adapter (`loopFamily`, `nextAction`, `evidenceRefs`, `entityType`,
`entityId`, `actionHref`).

## Verification Handoff

Parent verification already recorded:

- `npm run build` in `/home/lupin/code/execute-plans` passed.
- Local preview F04 Playwright passed twice:
  `PANTHEON_FE_BASE_URL=http://127.0.0.1:4174 npx playwright test e2e/04b-optimization-loop.spec.ts --trace=on --reporter=list --output=/tmp/fe-int-gate-align-f04-followup-run1`
  and the same command with `--output=/tmp/fe-int-gate-align-f04-followup-run2`.
- Hosted Lovable/dev BFF F04 Playwright passed twice:
  `PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io npx playwright test e2e/04b-optimization-loop.spec.ts --trace=on --reporter=list --output=/tmp/fe-int-gate-align-f04-followup-hosted-run2`
  and the same command with `--output=/tmp/fe-int-gate-align-f04-followup-hosted-run3`.
- Closeout verification recorded a hosted Lovable/dev BFF run at
  `/tmp/fe-int-gate-align-f04-followup-closeout-hosted-codex`.

Suggested reviewer checks for this sidecar packet:

```bash
sed -n '1,260p' support/sidecars/FE-INT-GATE-ALIGN-F04-FOLLOWUP/FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF.md
jq '.tasks[] | select(.id=="FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,220p' ai-task-archive/tasks/FE-INT-GATE-ALIGN-F04.json
sed -n '1,240p' ai-task-archive/tasks/FE-INT-GATE-ALIGN-F04-FOLLOWUP.json
git diff --check -- support/sidecars/FE-INT-GATE-ALIGN-F04-FOLLOWUP/FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF.md
```

Sidecar verification before handoff passed:

```bash
git diff --check -- support/sidecars/FE-INT-GATE-ALIGN-F04-FOLLOWUP/FE-INT-GATE-ALIGN-F04-FOLLOWUP-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Please review this packet as a support-only handoff for
`FE-INT-GATE-ALIGN-F04-FOLLOWUP`. The parent implementation is already closed;
the expected review question is whether this packet now gives the parent owner
and future BFF/frontend workers enough concrete context to preserve the
row-level optimization approval/HIQ journey without creating a duplicate
canonical/runtime task.
