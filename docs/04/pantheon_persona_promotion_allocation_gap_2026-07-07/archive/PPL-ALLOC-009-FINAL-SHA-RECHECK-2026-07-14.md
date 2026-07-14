# PPL-ALLOC-009 Final-SHA Closeout Recheck - 2026-07-14

Status: **blocked; not ready for `review` or `done`**

Evidence captured at `2026-07-14T02:10:50Z`. The machine-readable companion is
`PPL-ALLOC-009-FINAL-SHA-RECHECK-2026-07-14.json`.

## Decision

All formal child tasks are terminal and their primary implementation commits
are included in the deployed backend release or the deployed frontend `dev`
commit. The create-paper, human-decision, governed apply, authoritative Capital
readback, restart persistence, and safe containment subchains have each been
proved.

The packet still does not meet its explicit full-path acceptance. The current
quarterly ranking has complete allocation-policy fields but contains no
eligible live/canary Persona. The separately proven live Capital allocation
belongs to `persona-ppl011-e103d29e`, which returns HTTP 404 from the Persona
authority. Therefore no single governed identity joins a real ranking response
to target weights, proposal, approval/apply, receipt, and Capital readback.
Combining those independent subchains would overstate the evidence.

This worker did not manufacture or directly seed a live Persona. Doing so would
bypass the paper-to-canary-to-live governance boundary that this packet is
supposed to prove.

## Delivery And Deployment Identity

| Surface | Verified state | Verdict |
| --- | --- | --- |
| Pantheon dependency delivery | Primary terminal commits for PPL-ALLOC-002 through 008 and 011 through 013 are ancestors of `7475a06873202970dc6a827e4645430b192a536a`. | Pass |
| BFF deploy | Release `release/v2026.07.14.0` deploy run `29298791952` succeeded; its log verifies checkout, deploy, and BFF source SHA `7475a06873202970dc6a827e4645430b192a536a`. Public `/bff/version` reports that exact SHA with `source_commit_known=true`. | Pass |
| Later direct `dev` deploy | Run `29299038637` targeting `27cd4652995a53089c77e7c3613bf0cd955971f4` failed before deploy because the managed VM checkout contained untracked `trade_journey_events.json`. The running BFF remained on release SHA `7475a068...`. | Blocking deploy hygiene issue |
| Frontend deploy | `deployment.json` reports Execute Plans `b5d64856c9be1caa32078253a9f3758ed5abe07c`, deployed at `20260714T015854Z` from `dev`. Deploy run `29299854068` and branch gate `29299854021` passed. | Pass for frontend artifact |
| Frontend runtime mode | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`, and `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`. | Safe viewer-only configuration |
| Cross-surface identity | The frontend manifest reports BFF commit `27cd46529c29801db02818aafe4df723cc0f8666`; it is not the running `7475a068...`, does not equal the failed run's `27cd4652995...`, and is not resolvable in the fetched Pantheon repository. | Blocked until manifest provenance is corrected |
| Current frontend integration gate | Run `29299854029` for `b5d64856...` had passed lint, tests, build, contract drift, authenticated smoke, dry-run write probe, deep validation, browser probe, and route-load baseline; hosted acceptance was still running at capture time and Playwright remained pending. | Not final at capture time |

The direct `dev` deployment failure did not roll back or replace the successful
release deployment. It is recorded because the manifest claims a different BFF
identity and because the dirty managed worktree prevents a clean follow-up
publish.

## Child Delivery Ledger

Canonical terminal records live under
`ai-task-archive/tasks/PPL-ALLOC-*.json`. The primary delivery ledger is:

| Task | Repository / PR | Merge commit |
| --- | --- | --- |
| PPL-ALLOC-002 | Pantheon #3104 | `a8005fbb673ece7c86a7bf08a84687b0017b91e0` |
| PPL-ALLOC-003 | Pantheon #3105 | `ffe83a8fcd3a87a6266cf21c56d03fe466a7260d` |
| PPL-ALLOC-004 | Pantheon #3112 | `cec3660e4ba377cafc8388dd03d8346decdbdc4d` |
| PPL-ALLOC-005 | Execute Plans #248 / Pantheon #3109 | `f25cfdf06b03fb7d57219494cc744f5fdf7582de` / `51eab2627af83e312b45ba3a96b49b5145fd1598` |
| PPL-ALLOC-006 | Execute Plans #251 / Pantheon #3240 | `f1f62995c14ccb8dcba47390cd31d1f2c92bc5c0` / `a30ee14056b5fbc858a70f6c77696c0309405c95` |
| PPL-ALLOC-007 | Execute Plans #285 / Pantheon #3490 | `c62c0e8b9a49643c42f67614c542578afb233e84` / `7c179f4d5124cf389af068551daed2441b0f694b` |
| PPL-ALLOC-008 | Pantheon #3113 | `daeeb7733764f3e73cab15d9b4ee0efcebc1014b` |
| PPL-ALLOC-011 | Pantheon #3536 / final #3571 | `0e8c06603eb7ede8fd226837e439282e70fefc80` / `e13228e74b74e3bac1390efdbf5cbbe7425ad988` |
| PPL-ALLOC-012 | Pantheon #3556 / final #3577 | `f1576cf29f4980329cfda80cff01e91885467486` / `a52e3dab758c57fd2ce72dfdf5a008efa58385af` |
| PPL-ALLOC-013 | Execute Plans #304 / Pantheon #3532 | `36b745b1a17cdf4d2683530717aac633c8007776` / `01812d2169aa8668e64acba5d03ad9e44f245aa1` |

Every Pantheon merge listed above is an ancestor of deployed release SHA
`7475a068...`; every Execute Plans merge is an ancestor of deployed frontend
SHA `b5d64856...`.

## Hosted Acceptance Recheck

### Create and paper-to-decision paths

The accepted 2026-07-13 evidence remains the proof for these non-blocking
subchains:

- create-paper-bundle produced `persona-20260713-b4e3818e` in
  `paper_running`, with an isolated paper ledger, paper runtime and binding,
  no real pool, and `live_capital_side_effects=false`;
- promotion review
  `pm12-2026-q3-persona-20260528-cfedeed5-reduce_capital_access` was submitted,
  surfaced in Human Inbox, rejected by a human decision, and read back without
  direct live-capital mutation.

Their sanitized request/response summary remains in
`PPL-ALLOC-009-HOSTED-EVIDENCE-2026-07-13.json`. All implementation commits for
those paths are ancestors of the current release.

### Current immutable ranking: contract pass, positive join blocked

`GET /bff/management/persona-league/rankings?period=quarter&criteria=overall`
returned HTTP 200 at `2026-07-14T01:57:38Z`:

- snapshot: `ranking-quarterly-2026-q3-689e0bb29c378f1130cd049a`;
- 24 rows, all carrying that same snapshot id;
- 14 `not_running` and 10 `paper_running` rows;
- zero `live_running`/live rows, zero eligible rows, and zero non-null current
  weights;
- every row carries `stage`, `deployment_stage`, `tier_id`, `overall_score`,
  `allocation_policy_input`, and `ranking_snapshot_id`.

This clears the old PPL-ALLOC-012 field-completeness defect. It does not provide
an eligible real-ranking input from which a target allocation and proposal can
be derived.

### Governed apply and Capital readback: independently persistent

At running BFF SHA `7475a068...`, read-only requests confirmed:

- rebalance `rb-20260713-9e640fe8e883` is `applied`, with approval
  `approval-ppl011-final-0e8c0660`, command
  `cmd-29641b43c51241a0a4938a086ca3e180`, and an owner-store proposal;
- its live line moved from `0.01` to `0.0101` and its receipt reads back
  `0.0101` from Capital with `authoritative_capital_readback=true`,
  `authoritative_capital_state_applied=true`,
  `canonical_write_authority=capital_service`, delivered audit, and
  `live_capital_side_effects=false`;
- the command remains `executed` with result `applied`;
- Capital pool `pool-ppl011-e103d29e` retains live-owner binding
  `binding-ppl011-e103d29e` for `persona-ppl011-e103d29e`;
- `GET /bff/personas/persona-ppl011-e103d29e` returns HTTP 404.

The apply subchain is durable and correctly governed, but its Persona is absent
from the Persona authority and therefore absent from the current ranking. Its
synthetic snapshot `ranking-ppl011-final-0e8c0660` cannot be substituted for a
response-derived quarterly ranking snapshot in PPL-ALLOC-009 acceptance.

### Emergency containment: pass

The final-SHA readback also preserves the admitted safe containment:

- Persona `persona-20260713-9e33e590` is `paper_running`, `frozen=true`, with
  authoritative containment state `frozen`;
- command `cmd-414820143c8240098d5eaceec8e923f9` remains `executed` for
  `freeze`, with a distinct two-man signature;
- baseline, current, and target weights are all `0.005`;
- the receipt is authoritative, decrease-only, audit-delivered, and reports
  `live_capital_side_effects=false`.

This satisfies the emergency breach/containment leg and does not require more
PPL-ALLOC-011 implementation.

## Frontend Routing And Browser Evidence

The deployed frontend contains all PPL implementation merges and runs in
live/strict viewer-only mode. The previously hosted Persona Fleet probe had five
intended BFF requests and responses, no failed or old-BFF requests, no fallback,
and no console/CORS errors. PPL-ALLOC-013 removed the hard-coded
`Crypto-Alt-Hunter` assertion.

The later Management Performance IA intentionally replaced the original
single `/management/promotion-allocation` workbench with canonical Rankings,
Governance Decisions, and Performance centers. That source-level supersession
is reviewed in its child tasks but still needs the PPL-ALLOC-009 closeout
reviewer to accept it against the original page-inventory contract. A current
desktop/mobile joined browser journey must be rerun after the governed
live/canary fixture and deployment identity are fixed; a generic route-load
gate cannot prove the missing ranking-to-apply correlation.

## Dependency Security Recheck

The deployed `b5d64856...` `package.json` and `package-lock.json` exactly match
the audited files (SHA-256 lock hash
`bf79a2d545ba11e3a3af51914538019566c678a50060270b7fccf4047be43705`).

- `npm audit --omit=dev --json`: 12 production findings — 7 high, 5 moderate,
  zero critical;
- runtime-relevant high findings include `react-router-dom` / `react-router` /
  `@remix-run/router` and `lodash`; runtime moderate findings include
  `dompurify` and direct dependency `echarts`;
- full `npm audit --json`: 21 findings — 1 critical, 12 high, 8 moderate; the
  critical package is dev-only `vitest`.

The production findings need remediation or an explicit, time-bounded Security
acceptance before the packet may claim production readiness.

## Local Validation

On the latest merged `origin/dev` base:

```text
python3 -m pytest \
  services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py \
  services/control-plane/bff/tests/test_bff_persona_allocation_policy.py \
  services/control-plane/bff/tests/test_bff_rebalance_proposals.py \
  services/control-plane/bff/tests/test_bff_emergency_containment.py \
  services/control-plane/bff/tests/test_ppl_alloc_012_ranking_projection.py -q
```

Result: `101 passed, 110 warnings in 68.24s`. Warnings are existing FastAPI
`on_event` and `datetime.utcnow()` deprecations. `git diff --check` passed.

## Blocking Residual Risks And Owners

| ID | Blocking gap | Owner | Expiry / recheck condition |
| --- | --- | --- | --- |
| B1 | No one governed Persona joins an eligible real/canary ranking row, RuntimeBinding/telemetry evidence, Capital binding, target calculation, proposal, approval/apply receipt, and authoritative readback. | Persona + Runtime/Telemetry + Capital/BFF owners | Before PPL-ALLOC-009 review; provision a safe governed fixture and archive one correlated response chain. |
| B2 | Frontend manifest BFF SHA disagrees with `/bff/version`; the attempted direct `dev` deploy is blocked by dirty runtime state in the managed checkout. | Platform deployment owner | Before the next dev frontend publish and before PPL-ALLOC-009 review; clean/isolate runtime state, deploy, and verify both identities. |
| B3 | No current deployed-SHA desktop and mobile browser journey proves the joined PPL flow after PPL-ALLOC-013 and the latest frontend changes. | Frontend QA | After B1/B2 and before PPL-ALLOC-009 review; rerun with the governed fixture and archive route/request evidence. |
| B4 | Deployed production dependency graph has 7 high and 5 moderate audit findings. | Frontend Platform + Security | Before production-ready declaration; remediate or record explicit time-bounded risk acceptance. |
| B5 | The original primary-workbench page contract was superseded by the canonical-center IA. | Reviewer Codex | At PPL-ALLOC-009 review, after evidence blockers B1-B4 are cleared; explicitly accept the supersession or reopen the page target. |

## Required Next Action

Keep PPL-ALLOC-009 `blocked`. First create a safe, governance-produced
live/canary evidence fixture shared by Persona, RuntimeBinding/telemetry, and
Capital. From one immutable quarterly ranking response, calculate target
weights, create and approve/apply the rebalance, and archive the authoritative
receipt/readback. Then reconcile deployment identities, rerun current desktop
and mobile browser evidence, resolve or accept the production dependency risk,
and ask Codex for the explicit IA supersession decision.
