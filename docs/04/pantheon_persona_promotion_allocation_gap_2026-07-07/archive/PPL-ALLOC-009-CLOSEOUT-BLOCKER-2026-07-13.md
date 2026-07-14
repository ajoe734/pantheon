# PPL-ALLOC-009 Closeout Evidence And Blocker - 2026-07-13

Status: PPL-ALLOC-011 execution S1 blockers resolved; PPL-ALLOC-009 remains
blocked on the explicitly retained S2 review/security items below

## Final-SHA Recheck Addendum - 2026-07-14

The current deployment and terminal child-task recheck is recorded in
`PPL-ALLOC-009-FINAL-SHA-RECHECK-2026-07-14.md` and its sanitized companion
`PPL-ALLOC-009-FINAL-SHA-RECHECK-2026-07-14.json`.

PPL-ALLOC-012 has superseded the historical statement below that ranking rows
omit the allocation-policy join fields. The final-SHA ranking now carries a
single immutable snapshot id, stage, policy input, and weight fields for every
row. The remaining blocker is narrower: the hosted ranking has no eligible
live/canary row, while the independently proven Capital apply fixture has no
corresponding Persona authority record. Consequently there is still no one
governed identity that correlates `real ranking -> target weights -> proposal
-> approval -> apply receipt` end to end. The execution and containment S1
repairs remain closed.

## PPL-ALLOC-011 Resolution Addendum - 2026-07-13

PPL-ALLOC-011 has closed the three execution-plane S1 blockers and the BFF
version-evidence gap recorded in this packet. The final guarded-admission repair
merged in PR #3536 at
`0e8c06603eb7ede8fd226837e439282e70fefc80`. Exact-SHA root deploy run
`29268814057` and exact-SHA BFF restart run `29270122636` both succeeded.

Hosted acceptance at that SHA proved:

- an unapproved `live_running` apply remains HTTP 409;
- two distinct operators, a bound approval, and a bound confirmation token
  admit the apply, which reaches `executed/applied`;
- proposal, receipt, audit references, and the Capital owner readback agree on
  rebalance `rb-20260713-9e640fe8e883`, command
  `cmd-29641b43c51241a0a4938a086ca3e180`, and target weight `0.0101`;
- `authoritative_capital_readback=true`,
  `authoritative_capital_state_applied=true`,
  `canonical_write_authority=capital_service`, and
  `live_capital_side_effects=false`;
- the confirmation token is consumed atomically: the exact same idempotency key
  safely replays the same command, while a new key reusing the token fails with
  HTTP 428 `CONFIRM_TOKEN_INVALID`;
- after the exact-SHA restart, the same proposal, command, receipt, allocation,
  and redeemed-token identities remain readable and replay-safe;
- the earlier safe containment command
  `cmd-414820143c8240098d5eaceec8e923f9` remains terminal and the Persona remains
  authoritatively frozen; promotion still fails HTTP 422 and freeze without a
  confirmation token still fails HTTP 428; and
- `/bff/version` reports the exact deployed source commit SHA.

The sanitized evidence is archived in
`PPL-ALLOC-011-HOSTED-EVIDENCE-2026-07-13.json` beside this document.

This addendum clears only the execution/persistence/containment S1 rows and the
runtime-version S3 row originally assigned to PPL-ALLOC-011. It does **not**
clear the ranking-to-proposal join, frontend IA supersession review, dependency
vulnerability, or unrelated QA/warning-debt items. PPL-ALLOC-009 therefore
remains blocked pending those retained owners and review decisions.

## Historical Decision (superseded for the PPL-ALLOC-011 scope)

The following decision records the state before the resolution addendum above.
Its apply/readback/restart/containment conclusions are retained as provenance,
not as the current execution-plane verdict. The ranking, frontend IA, and
dependency-security blockers remain current.

The packet is not ready for `review` or `done`.

The create-to-paper and recommendation-to-human-decision paths pass on hosted
dev. The allocation path passes policy evaluation, proposal persistence, and
the human-approval admission gate, but its approved apply command never leaves
`submitted` and no authoritative capital readback reports the target weight.
Emergency promotion/allocation-increase attempts fail closed, but a safe
freeze requires a distinct second operator signature that this worker is not
authorized to manufacture. Those are blocking gaps against the execution
packet's required end-to-end proof.

Sanitized probe results are recorded in
`PPL-ALLOC-009-HOSTED-EVIDENCE-2026-07-13.json` in this directory.

### Redispatch recheck

The supervisor redispatched the task after all formal dependencies became
terminal. A read-only hosted recheck at `2026-07-13T04:35:50Z` confirmed that
this did not clear the execution blocker:

- `GET /healthz` returned HTTP 200 and reported all named dependencies ready.
- `GET /api/v1/operator/commands/7a3e7310-0596-4805-81d6-40b75fd7a412`
  returned HTTP 200 with `status=submitted`, `result=null`, and `error=null`.

No capital-affecting command was issued during this recheck. The required
terminal execution/readback and legitimate second-operator containment proof
remain outstanding.

### Local terminal-receipt repair candidate

The redispatch identified that the proposal apply route persisted a command
but never registered the existing background command processor. A narrow local
repair now queues that processor and preserves the rebalance entity identity
in the execution params. Its regression test proves the command reaches
`executed` with an adapter receipt that explicitly reports
`live_capital_side_effects=false`.

PR #3493 merged this repair at
`276eb9c0312871aeb2ebb2f14545720da040e46a`. Dev deploy run `29225028783`
succeeded, including VM deployment, public BFF smoke, and Agora restart
persistence smoke.

A hosted recheck then created proposal `rb-20260713-002`. Apply without an
approval reference remained fail-closed with HTTP 409. Apply with the dev
approval reference returned HTTP 202 and command
`a729a21d-2d92-40ad-a8f4-95cf767cbac7` reached `executed`. Its result preserved
`action_id=apply`, `entity_type=Rebalance`, `entity_id=rb-20260713-002`, and
`live_capital_side_effects=false`. This closes the stuck terminal-receipt gap.

It does not prove authoritative allocation application. Proposal readback
remained `status=pending`, `applied=false`, and explicitly degraded from
`local_snapshot`. The pre-deploy proposal `rb-20260713-001` also returned 404
after the BFF restart, exposing a separate proposal-persistence gap. The
Capital Pool / Execution Plane write contract, restart-safe proposal authority,
and post-apply allocation readback remain blocking evidence.

## Dependency And PR Ledger

All implementation dependencies have durable `done` records. The relevant
delivery PRs are merged:

| Slice | Repository | PR | Merge commit | Gate evidence |
| --- | --- | ---: | --- | --- |
| Current-state audit | `ajoe734/pantheon` | #3103 | `6acbf5d074941a3f0c6b8f51f18b53bca24466fd` | Branch CI passed |
| Create paper bundle | `ajoe734/pantheon` | #3104 | `a8005fbb673ece7c86a7bf08a84687b0017b91e0` | Branch CI passed |
| Binding read model | `ajoe734/pantheon` | #3105 | `ffe83a8fcd3a87a6266cf21c56d03fe466a7260d` | Branch CI passed |
| Allocation policy / proposal | `ajoe734/pantheon` | #3112 | `cec3660e4ba377cafc8388dd03d8346decdbdc4d` | Branch CI passed |
| Emergency policy | `ajoe734/pantheon` | #3113 | `daeeb7733764f3e73cab15d9b4ee0efcebc1014b` | Branch CI passed |
| Create Paper Persona UI | `ajoe734/execute-plans` | #248 | `f25cfdf06b03fb7d57219494cc744f5fdf7582de` | integration gate run `29139993234` passed |
| Workbench UI | `ajoe734/execute-plans` | #251 | `f1f62995c14ccb8dcba47390cd31d1f2c92bc5c0` | integration gate run `29158460442` passed |
| Workbench closeout record | `ajoe734/pantheon` | #3240 | `a30ee14056b5fbc858a70f6c77696c0309405c95` | Branch CI passed |
| Binding visibility / route prune UI | `ajoe734/execute-plans` | #285 | `c62c0e8b9a49643c42f67614c542578afb233e84` | integration gate run `29222175376` passed |
| Binding visibility closeout record | `ajoe734/pantheon` | #3490 | `7c179f4d5124cf389af068551daed2441b0f694b` | Branch CI passed |

## Deployment Identity

- Frontend `deployment.json` returned commit
  `c62c0e8b9a49643c42f67614c542578afb233e84`, deployed at
  `20260713T040033Z` from `dev`.
- The frontend reports `VITE_BFF_MODE=live` and
  `VITE_BFF_FALLBACK=strict`.
- Execute Plans dev push gate run `29222851079` passed all steps, including
  lint, tests, build, contract drift, authenticated BFF smoke, hosted
  production acceptance, and Playwright.
- Pantheon dev deploy run `29216864125` succeeded from
  `72a61ceb43ab970bdeabb3eae7938920b8898379`; every Pantheon implementation
  merge above is an ancestor of that SHA. The public BFF health endpoint
  returned HTTP 200.
- The BFF has no deployment/version endpoint carrying a source SHA. The
  workflow SHA plus deployed response behavior is the available identity
  evidence; this limitation is recorded below.

## Hosted Journey Results

### Create persona to isolated paper runtime: pass

The first `create-paper-bundle` call timed out after 30 seconds with no
response. Before retrying, Fleet readback found exactly one matching persona.
Replaying the original request with the same idempotency key returned HTTP 201
and the same identity:

- persona: `persona-20260713-b4e3818e`
- state: `paper_running`
- capital mode / deployment stage: `paper` / `paper`
- paper ledger: `paper-ledger-persona-20260713-b4e3818e`
- runtime: `runtime-persona-20260713-b4e3818e-paper`
- runtime binding: `binding-persona-20260713-b4e3818e-paper`
- real capital pool: absent
- required data-source binding count: 2
- `live_capital_side_effects=false`
- `human_review_required_for_live=true`

This proves both the paper bundle and the ambiguous-transport idempotency
behavior without introducing real-capital intent.

### Paper recommendation to human decision: pass

The dev-only governed write probe submitted recommendation
`pm12-2026-q3-persona-20260528-cfedeed5-reduce_capital_access`, persisted its
Human Inbox item, recorded a `reject` decision, and read the decision back.
Submit returned 200, decision admission returned 202, and detail/inbox reads
returned 200. The receipt reports `live_capital_mutation=false`.

### Real allocation proposal and apply: blocked

The policy evaluator produced an auditable live-stage contract line from
snapshot `ppl-alloc-009-20260713T041416Z`:

- current / target / delta: `0.04` / `0.05` / `0.01`
- cap reasons: `live_b_tier_cap`, `quarterly_increase_cap_25pct`
- human approval required: true
- rebalance: `rb-20260713-001`

Proposal create returned 202 and detail readback preserved the snapshot,
weights, simulation, constraints, rollback target, and `applied=false`.
Apply without approval correctly returned 409 `PRECONDITION_FAILED`. Apply
with a dev approval reference returned 202 and command
`7a3e7310-0596-4805-81d6-40b75fd7a412`.

Three later command polls still returned `submitted`; the rebalance remained
`applied=false`, with no approval reference or target-weight update in its
authoritative read. In addition, the hosted quarterly ranking projection does
not expose the stage/current-weight/evidence tuple required to derive this
proposal from one immutable live ranking response. The probe therefore proves
the contract and admission gates, not real allocation execution.

### Emergency containment: fail-closed pass, safe execution blocked

Hosted `EmergencyContainment` commands attempting `promote_to_live` and
`increase_allocation` both returned HTTP 422 with
`Emergency containment cannot promote or increase allocation`.

A safe `freeze` correctly returned 428 without a confirmation token. After a
bound dev confirm token was created, it returned 409
`TWO_MAN_SIGNATURE_REQUIRED`. A distinct second operator was not supplied;
this worker did not forge that authorization. Thus the safety boundary passes,
but the required admitted safe containment receipt and post-state readback
remain unproven.

## Frontend And Page Inventory

The hosted Persona Fleet browser probe passed with five intended BFF requests,
five responses, zero failed requests, zero old-BFF hits, no seed fallback, and
no console/CORS errors.

The original page inventory has subsequently been consolidated by the
Management Performance IA route manifest. This is the observed deployed state:

| Original surface | Deployed state | Verdict against original gap spec |
| --- | --- | --- |
| `/management/promotion-allocation` | Redirects by tab to Rankings or Governance Decisions | Reviewed IA supersession is present in source, but it is not the original primary-workbench target; reviewer acceptance is required. |
| `/management/persona-fleet` | Primary monitoring page; hosted live/strict probe passed | Pass. |
| `/management/personas` | Registry and Create Paper Persona entry | Pass; hosted create command passed. |
| `/management/personas/:id/onboarding` | Detail/setup-repair route remains | Pass at source level. |
| `/management/human-inbox` and detail | Governed review queue/detail | Pass; submit, reject, and exact inbox readback passed. |
| `/management/capital` | Redirects to Performance `exposure`; detail route remains | Later IA supersession; binding identity is visible, but original list target was replaced. |
| `/management/rebalance/:id` | Canonical detail route remains | Pass at source level. |
| `/management/ranking` | Redirects to Governance Decisions `policy` | Pass as a non-primary diagnostic/policy alias. |
| `/management/readiness/capital-binding-live` | Redirects through the quarterly-capital compatibility route | Pass as a readiness-only alias. |
| `/management/persona-league` | Redirects to Rankings `rolling` | Pass. |
| `/management/quarterly-ranking` | Redirects to Rankings `quarterly` | Pass. |
| `/management/rebalance` and `/management/rebalances` | Redirect to Governance Decisions `capital` | Pass; detail route remains separate. |

The created-persona linked-page Playwright test reached the focused Rankings
row and rendered `PPL-ALLOC-009 Dev Smoke 20260713T041132Z`, then failed because
the test hard-codes the unrelated name `Crypto-Alt-Hunter`. The deployed PR
gate passed using its default fixture, so this is a harness defect exposed by
the task-specific persona, not evidence that the new row failed to render.

## Validation

Pantheon:

```sh
python3 -m pytest \
  services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py \
  services/control-plane/bff/tests/test_bff_persona_allocation_policy.py \
  services/control-plane/bff/tests/test_bff_rebalance_proposals.py \
  services/control-plane/bff/tests/test_bff_emergency_containment.py -q
```

Result: 25 passed, with eight existing FastAPI `on_event` deprecation
warnings.

Execute Plans, from a clean detached worktree at deployed SHA `c62c0e8...`:

```sh
npm ci
npm test -- \
  src/management/pages/oversight/PromotionAllocation.test.tsx \
  src/management/pages/oversight/PersonaFleetPage.test.tsx \
  src/management/pages/oversight/HumanGateDetail.test.tsx
npm run lint
npm run build
```

Results: 30 tests passed; lint passed with 57 warnings and zero errors; build
passed with existing circular-chunk, CSS-minification, stale Browserslist, and
large-chunk warnings. `npm ci` reported 21 dependency vulnerabilities (8
moderate, 12 high, 1 critical).

Hosted browser commands:

```sh
PANTHEON_AUDIT_OUT_DIR=/tmp/ppl009-browser \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
node scripts/probe-hosted-browser-bff.mjs

PANTHEON_PERSONA_FLEET_AUDIT_ID=persona-20260713-b4e3818e \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
npx playwright test e2e/25-persona-fleet-live-linked-pages.spec.ts \
  --project=chromium --workers=1 --reporter=list
```

The first command passed. The second failed only at the hard-coded persona-name
assertion described above after rendering the task persona.

## Residual Risks And Owners

| Severity | Blocking | Risk | Owner | Objective expiry / recheck |
| --- | --- | --- | --- | --- |
| S1 | No — resolved by PPL-ALLOC-011 | Governed allocation write authority now reaches `executed/applied`, and proposal/Capital owner reads report the same authoritative applied weight and identities. | Capital Plane / Execution Plane | Recheck only if the owner API or command executor contract changes. |
| S1 | No — resolved by PPL-ALLOC-011 | Proposal, command receipt, bound approval reference, allocation, and redeemed-token identities survived exact-SHA BFF redeploy and remained replay-safe. | BFF / Capital Plane persistence | Retain single-writer and host-volume caveats; add separate multi-replica/DR work before making broader guarantees. |
| S1 | No — resolved by PPL-ALLOC-011 | A genuine distinct second operator admitted safe freeze; terminal receipt and authoritative frozen Persona state survived later deploys. | Human/Ops + Risk Owner | Recheck only if containment admission or owner mutation contracts change. |
| S2 | Yes | Hosted ranking rows omit stage, current weight, and evidence fields needed for a response-derived ranking-to-proposal join. | BFF Allocation Read Model | Recheck after one immutable ranking response carries the complete allocation universe and proposal join fields. |
| S2 | Yes | Original single Promotion & Allocation workbench contract was replaced by later IA redirects; the supersession needs explicit closeout reviewer acceptance. | Management Frontend + Claude | Resolve during PPL-ALLOC-009 review by accepting the newer canonical-center model or reopening the original route target. |
| S2 | Yes | `npm ci` reports 1 critical and 12 high dependency vulnerabilities. | Frontend Platform / Security | Triage package-level reachability and remediation before this packet is declared production-ready. |
| S3 | No — resolved by PPL-ALLOC-011 | `/bff/version` exposes and was verified against the exact workflow target SHA. | Platform Deployment | Keep the deploy-time source-SHA assertion in the nonprod workflow. |
| S3 | No | Linked-page hosted test hard-codes `Crypto-Alt-Hunter`. | Frontend QA | Replace with the selected live persona name and rerun against the task-created persona. |
| S3 | No | Existing FastAPI, lint, Rollup/CSS, Browserslist, and chunk-size warnings remain. | BFF / Frontend Platform | Track in platform warning-debt backlog; focused gates remain green. |

## Current Required Next Action

Keep `PPL-ALLOC-009` in progress only for the retained S2 blockers. The BFF
Allocation Read Model owner must provide the immutable ranking-to-proposal join;
the Management Frontend reviewer must accept or reopen the IA supersession; and
Frontend Platform/Security must triage the recorded dependency vulnerabilities.
The execution, restart persistence, safe containment, and runtime-version work
assigned to PPL-ALLOC-011 requires no further implementation unless its owner
contracts change.
