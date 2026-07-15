# PPL-ALLOC-009 Rescue Recheck - 2026-07-15

Status: **blocked; not ready for `review` or `done`**

Evidence captured through `2026-07-15T06:21:22Z`. The machine-readable
companion is `PPL-ALLOC-009-RESCUE-RECHECK-2026-07-15.json`.

## Decision

The rescue work repaired two concrete delivery defects:

- Execute Plans PR #353 removed the mutable/stale BFF SHA input from normal
  deploys, made `/bff/version` the runtime identity authority, added
  gate-before-switch and rollback protection, and shipped a zero-finding
  production dependency graph.
- Pantheon anchor `20fd88f91b6b813471272e4297f45f999f114e62`
  adds a workflow-level strict-auth floor so a requested historical ref cannot
  execute an older permissive deploy script before current safety checks run.

The packet remains blocked. There is still no single governance-produced
Persona identity joining the canonical quarterly ranking through Runtime and
Telemetry evidence, target calculation, proposal, distinct approval/apply,
and authoritative Capital readback. The current hosted BFF also regressed from
strict auth to `permissive`/stub when a successful workflow dispatch deployed
an old target ref. Exact FE/BFF SHA equality alone is not sufficient while that
security posture is unsafe. A generic browser/deployment probe cannot replace
the missing authenticated desktop/mobile joined journey, and reviewer Claude
has not accepted the canonical-center IA as a replacement for the original
workbench target.

Independent create-paper, promotion-decision, governed Capital apply, and
containment subchains remain valid evidence for their own legs. They are not
combined here into a fabricated end-to-end identity.

## Correction To The 2026-07-14 Evidence

The previous recheck treated this response as quarterly-ranking evidence:

```text
GET /bff/management/persona-league/rankings?period=quarter&criteria=overall
```

That classification was wrong. The route implementation always attaches a
`rolling` snapshot with period `short_cycle`; `period=quarter` is only a common
item filter. The canonical quarterly surface is:

```text
GET /bff/management/quarterly-ranking?quarter=2026-Q3
```

Therefore the 2026-07-14 row count, stage count, and snapshot ID are withdrawn
as *quarterly hosted* evidence. The field-completeness implementation tests
remain useful, but B1 requires a fresh governed response from the canonical
route. After the latest root deploy, anonymous calls correctly returned 401;
a fixed permissive-mode viewer stub could read `/bff/me`, but canonical and
rolling ranking reads returned no bytes within 20 seconds. Those diagnostic
calls used a non-governed stub and are not acceptance evidence.

## Child Delivery Ledger

The terminal child ledger from the prior closeout was rechecked and remains
unchanged:

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

## Rescue Delivery Delta

### Execute Plans

PR `ajoe734/execute-plans#353` merged to `dev` at
`2026-07-15T05:14:14Z` as
`cf3578d3bd3f64fc07ccefe4ae7338608bc49c66`.

Delivered behavior:

- normal deploys no longer inherit repository variable `PANTHEON_BFF_SHA`;
  that stale variable was removed after deployment;
- initial, immediately-before-switch, and immediately-after-switch BFF
  identities must all be known and equal;
- an optional expected BFF SHA is a comparator, never the runtime truth;
- the live symlink switch is lock-protected, checks its expected predecessor,
  and rolls back on post-switch identity or browser-probe failure;
- `deployment.json` records the exact runtime BFF SHA and safe write/token
  defaults;
- ECharts and affected production dependencies were remediated without
  changing the six supported chart-series contracts.

Commit-trailer, generated-file, and smoke checks passed. PR integration run
`29390879310` failed at the first identity step because `/bff/version` returned
HTTP 502 four times during a concurrent root redeploy; it is not described as
a passing PR check. The independent push deployment run `29390934575`
succeeded for the merge SHA.

Execute Plans then advanced to
`79e0f8f3083c8546ec2c139afbc339322dcbe755` via PR #349. That commit changes
only `e2e/evochain009.spec.ts`, has `cf3578d3...` as an ancestor, and retains
the exact same `package-lock.json`. Push deployment run `29392291433`
succeeded and is the current hosted FE release. Integration run `29392291419`
also completed successfully with stable FE `79e0f8f3...` / BFF `a10f752b...`
identity. Its unit suite passed 154 files / 1427 tests; Playwright reported 170
passed, 62 skipped, zero unexpected failures, and zero flaky tests.

That gate is useful but scoped. It ran with the workflow's default
`PANTHEON_RELEASE_GATE_AUTH_MODE=permissive` against a BFF reporting
`auth_stub=true`. Its browser probe used a local FE from the same source SHA at
`127.0.0.1:4173` proxying to the hosted BFF, not the hosted FE origin. The PINT
governed-proposal smoke and PINT desktop/mobile write proof were skipped. The
aggregate artifact reports `overall=warn`, including uncovered create dry-run
routes, allowed skipped performance/F10 specs, and source-scan receipt
warnings. It therefore does not clear strict auth, B1, or the governed B3
desktop/mobile journey despite the workflow conclusion `success`.

### Pantheon BFF and deployment safety

Pantheon run `29390952944` completed successfully. Its run metadata head was
`1fef00eb7f23da05fd964087db85426863331540`, but its explicit workflow input,
checkout, and actual `dev/root` deployment target were
`a10f752b3ea4420f271535e255f2d4e7d3d498b2`. The log verified that exact BFF
source SHA, OpenClaw live smoke, public health/CORS, Agora restart persistence,
and healthy Runtime Manager, Governance, and Deployment dependencies.

The same run also exposed a safety hole: because the checked-out historical
deploy script predates the strict-auth defaults, the successful deploy changed
the hosted posture from `auth_stub=false, auth_mode=strict` to
`auth_stub=true, auth_mode=permissive`. GitHub currently has none of the three
required `DEV_BFF_JWT_SECRET`, `DEV_BFF_OIDC_CLIENT_ID`, and
`DEV_BFF_OIDC_CLIENT_SECRET` repository/environment secrets. The existing
fail-closed deploy contract correctly states that a human must provision them;
this worker did not generate, recover, or substitute credentials.

Anchor `20fd88f91...` moves the credential and strict-contract checks into the
workflow before any remote deployment. It prevents another historical target
ref from bypassing the current deploy script's preflight, but it cannot repair
the already-running permissive service without governed credentials.

## Hosted Deployment Identity At Cutoff

| Evidence | Current value | Verdict |
| --- | --- | --- |
| Hosted FE commit/source ref | `79e0f8f3083c8546ec2c139afbc339322dcbe755` | Contains Execute Plans PR #353 |
| Manifest BFF commit | `a10f752b3ea4420f271535e255f2d4e7d3d498b2` | Runtime-derived, evidence flag true |
| `/bff/version` source SHA | `a10f752b3ea4420f271535e255f2d4e7d3d498b2`, `source_commit_known=true` | Exact cross-surface match |
| BFF health | `/readyz` 200; Runtime Manager, Governance, Deployment healthy | Service dependencies ready |
| BFF auth posture | `auth_stub=true`, `auth_mode=permissive`, dev login disabled | **Unsafe regression; B2 remains open** |
| FE runtime mode | `live` + `strict` fallback | Correct BFF routing mode |
| FE write defaults | real writes false; dev stub writes false | Viewer-safe build default |
| FE token default | embedded bearer false | No bundled dev bearer |

The deployment wiring and exact identity subcheck now pass. B2 as a whole does
not pass because current hosted authentication is no longer strict.

## Dependency Security

The current hosted FE commit and PR #353 both contain lock SHA-256
`2d796e302699e72c9b0e731f4bab23b07eefbc007cfbe795dcfc43cd1ff778ad`.

- `npm audit --omit=dev --json`: zero findings at every severity.
- Full `npm audit --json`: 9 findings — 1 critical, 5 high, 3 moderate — in
  the dev/toolchain graph omitted from the production build audit.
- The shipped versions include ECharts/zrender 6.1.0, React Router 6.30.4,
  `@remix-run/router` 1.23.3, lodash 4.18.1, and DOMPurify 3.4.12.

B4 is cleared only for the defined deployed **production dependency graph**.
This document does not claim the full npm graph is vulnerability-free.

## Validation

Pantheon:

- focused create-paper, promotion review, allocation policy, rebalance, and
  containment suite: `105 passed, 22 warnings in 149.18s`;
- strict-auth deployment contract after the historical-ref guard:
  `6 passed in 6.27s`;
- workflow YAML parse and `git diff --check`: passed.

Execute Plans:

- complete Vitest run: 154 files, 1427 tests passed;
- chart renderer focus: 22 tests passed, covering graph, sankey, candlestick,
  gauge, heatmap, and scatter without `lines` substitution;
- actual ECharts 6 server-side render smoke produced SVG for all six series;
- production build passed with existing non-blocking chunk/dynamic-import
  warnings;
- release identity tests and deploy behavioral harness passed, including
  pre/post drift, rollback, external switch, invalid predecessor, and lock
  contention cases;
- production audit: zero findings; full graph findings remain as scoped above.
- current dev integration gate `29392291419`: workflow success with stable
  exact release identity; 170 Playwright tests passed and 62 were skipped. Its
  permissive/stub auth context and skipped PINT write proofs prevent it from
  serving as strict-auth or joined-capital evidence.

## Blocking Residual Risks

| ID | Status | Blocking gap | Owner / recheck condition |
| --- | --- | --- | --- |
| B1 | Open, S2 | No one governed Persona/Runtime/Telemetry/Capital identity joins a canonical quarterly-ranking response through target, proposal, distinct approval/apply, and authoritative readback. | Persona + Runtime/Telemetry + Capital/BFF owners; create one safe governance-produced fixture and archive one correlated chain. |
| B2 | Open, S2 | FE/BFF SHA equality and safe FE defaults pass, but the hosted BFF is now permissive/stub and the governed strict-auth secrets are absent. | Human platform operator; provision the three named GitHub secrets, deploy a strict-contract ref, verify `/bff/version`, then republish FE if the BFF SHA changes. |
| B3 | Open, S2 | No current deployed-SHA desktop and mobile journey proves B1 with authenticated governed identities. The unauthenticated deployment probe is only wiring evidence. | Frontend QA after B1 and B2; archive route, request/response, identity, console, and mobile/desktop evidence. |
| B4 | Cleared in scope | Deployed production dependency graph now has zero audit findings. | Scope is production dependencies only; dev/toolchain findings remain separately visible. |
| B5 | Open, S2 | Canonical Rankings/Governance/Performance centers supersede the original primary-workbench contract without this task's reviewer decision. | Reviewer Claude; explicitly accept the IA supersession or reopen the workbench target. |

## Safety And Authorization Boundary

- No live/canary Persona was seeded or promoted by this rescue.
- No Capital allocation was changed.
- No approval, operator identity, or second operator was fabricated.
- The only fixed stub used after the auth regression was a viewer token for
  read-only diagnosis, and its responses are explicitly excluded from
  acceptance.
- No credential was printed, generated, rotated, recovered, or copied.

## Required Next Action

Keep PPL-ALLOC-009 `blocked`. First provision the three governed dev auth
secrets and run a BFF/root deployment whose target contains the strict-auth
contract; verify strict posture and exact source identity. Republish the FE if
that changes the BFF SHA and rerun the current integration gate. Then resolve
B1 with one canonical quarterly governance-produced identity, execute the
authenticated desktop/mobile B3 journey without live-capital side effects,
and obtain Claude's explicit B5 decision.
