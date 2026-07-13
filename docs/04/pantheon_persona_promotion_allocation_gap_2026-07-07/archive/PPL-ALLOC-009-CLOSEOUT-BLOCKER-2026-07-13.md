# PPL-ALLOC-009 Closeout Evidence And Blocker - 2026-07-13

Status: blocked after hosted dev acceptance

## Decision

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
| S1 | Yes | Approved rebalance command remains `submitted`; no terminal execution or authoritative applied-weight readback exists. | Control Plane / Execution Plane | Recheck when the command reaches a terminal state and Capital/Fleet reads return the same applied weights and identities. |
| S1 | Yes | Safe emergency freeze cannot be admitted without a distinct second authorized operator, so no containment receipt/post-state proof exists. | Human/Ops + Risk Owner | Re-run with two legitimate operators and preserve confirmation, signature, terminal receipt, and unchanged promotion/allocation readback. |
| S2 | Yes | Hosted ranking rows omit stage, current weight, and evidence fields needed for a response-derived ranking-to-proposal join. | BFF Allocation Read Model | Recheck after one immutable ranking response carries the complete allocation universe and proposal join fields. |
| S2 | Yes | Original single Promotion & Allocation workbench contract was replaced by later IA redirects; the supersession needs explicit closeout reviewer acceptance. | Management Frontend + Claude | Resolve during PPL-ALLOC-009 review by accepting the newer canonical-center model or reopening the original route target. |
| S2 | Yes | `npm ci` reports 1 critical and 12 high dependency vulnerabilities. | Frontend Platform / Security | Triage package-level reachability and remediation before this packet is declared production-ready. |
| S3 | No | BFF does not expose deployed source SHA at runtime. | Platform Deployment | Add a deployment/version endpoint and recheck against the workflow SHA. |
| S3 | No | Linked-page hosted test hard-codes `Crypto-Alt-Hunter`. | Frontend QA | Replace with the selected live persona name and rerun against the task-created persona. |
| S3 | No | Existing FastAPI, lint, Rollup/CSS, Browserslist, and chunk-size warnings remain. | BFF / Frontend Platform | Track in platform warning-debt backlog; focused gates remain green. |

## Required Next Action

Keep `PPL-ALLOC-009` blocked. The Control Plane / Execution Plane must first
make approved rebalance commands terminal and expose authoritative allocation
readback. Human/Ops and the Risk Owner must then execute the safe containment
probe with a genuine second signer. After the ranking join and route
supersession are reviewed, rerun the hosted journey and only then hand the task
to Claude for formal review.
