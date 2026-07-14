# AG-UIPOL-004 hosted evidence

Captured: 2026-07-14 04:06–04:07 UTC
Status: owner resubmission after the 2026-07-14 review blockers

This record proves the objective state-consistency defects in
`AG-UIPOL-004`; it does not claim recovered-design parity or close the later
AG-UIPOL-006+ parity work.

## Accepted deployment identity

The current Pantheon dev frontend manifest and the live BFF version endpoint
agree on both served revisions:

- frontend: `936f252e09fa3bb887c88e733e24b6941cac644e` on
  `ajoe734/execute-plans@dev`;
- BFF: `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` on
  `ajoe734/pantheon@dev`;
- build posture: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`, `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, and
  `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`.

The BFF identity is not inferred from the frontend alone. Live
`GET /bff/version` returns the same full 40-character SHA with
`source_commit_known=true`. The following delivery runs are green:

- execute-plans Branch CI
  [29304416763](https://github.com/ajoe734/execute-plans/actions/runs/29304416763);
- execute-plans dev FE deploy
  [29304416758](https://github.com/ajoe734/execute-plans/actions/runs/29304416758);
- Pantheon Branch CI
  [29303213895](https://github.com/ajoe734/pantheon/actions/runs/29303213895);
- Pantheon nonprod BFF deploy
  [29303223159](https://github.com/ajoe734/pantheon/actions/runs/29303223159).

The frontend deploy fetched the active BFF identity from `/bff/version`. The
BFF deploy independently verified its served source SHA and passed public,
CORS, and restart-persistence smoke checks.

## Delivery and repair chronology

- execute-plans PR
  [#290](https://github.com/ajoe734/execute-plans/pull/290) introduced active
  Ready styling, Unassigned ordering/explanation, and measured-versus-missing
  metric rendering.
- execute-plans PR
  [#295](https://github.com/ajoe734/execute-plans/pull/295) aligned the rail
  with the exact completeness card/snapshot identity.
- execute-plans PR
  [#325](https://github.com/ajoe734/execute-plans/pull/325) cleared
  workshop-scoped state across workshop changes and covered the list/table
  zero-versus-unreported semantics.
- execute-plans PR
  [#335](https://github.com/ajoe734/execute-plans/pull/335) guarded the real
  BFF payload that omits `subject` in both the page and Servant drawer, and
  removed the fabricated default BFF SHA from manifest generation.
- Pantheon PRs
  [#3596](https://github.com/ajoe734/pantheon/pull/3596),
  [#3597](https://github.com/ajoe734/pantheon/pull/3597),
  [#3598](https://github.com/ajoe734/pantheon/pull/3598), and
  [#3605](https://github.com/ajoe734/pantheon/pull/3605) repaired the managed
  deploy worktree, linked-worktree gitdir handling, dev auth propagation, and
  restart-smoke SIGPIPE failure that had prevented a trustworthy BFF deploy.

The earlier screenshots at frontend `12b78ef...`, unsafe write flags, false
`27cd46529c...` BFF identity, and failed deployment attempts are historical
and are superseded by this exact-identity capture.

## Workshop state-consistency proof

The browser loaded the real hosted route without response interception:

`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/strategy-workshop/b888fb96-12b4-46e1-8def-ffe4f29b5ad7`

The visible state is tied to workshop
`b888fb96-12b4-46e1-8def-ffe4f29b5ad7`, snapshot
`8f7dc9e4-108f-4067-8d05-9cad30c7e17a`, strategy version
`full003-postdeploy-1783268578-f4b6f0-v1`, and completeness card
`card_completeness_8f7dc9e4-108f-4067-8d05-9cad30c7e17a`.

Assertions:

- the completeness card says `complete` and `Research ready: Yes`;
- the rail says `Complete`, `100%`, and `Research ready: Yes`;
- all seven displayed dimensions say `Complete`;
- Preliminary research, Full validation, and Trading room each expose
  `data-readiness-state=ready` and active green classes rather than disabled
  styling;
- the BFF currently omits `subject`; the guarded UI renders
  `Subject: none (none)` with no page error;
- authenticated workshop, completeness, readiness, cards, and events reads
  all went to the manifest BFF origin and returned HTTP 200.

Screenshots:

- [full workshop consistency](./AG-UIPOL-004-workshop-consistency.png)
- [rail close-up](./AG-UIPOL-004-workshop-rail.png)
- [completeness-card close-up](./AG-UIPOL-004-workshop-completeness-card.png)

## Performance semantics proof

The browser loaded the real hosted `/agora/strategy-performance` route. The
page left its loading state in 12.2 seconds, emitted no console/page error,
and rendered live attribution.

Assertions:

- two named strategies render before the aggregate `Unassigned` row;
- `Unassigned` is the third row (`unassignedIndex: 2`), is marked
  attribution-only, and explains that it aggregates telemetry the BFF could
  not link to a named Trading Room strategy;
- current measured Unassigned values include `$0` and `7,409` trades;
- `$0` carries `data-metric-state=measured` and title `已量測：零`;
- absent values render `未回報` with
  `data-metric-state=not-reported` and title `BFF 未回報量測值`;
- the observed request provenance is the implemented route
  `GET /bff/management/performance-attribution/by-strategy?period=latest&page_size=50`,
  plus `GET /bff/agora/trading-room` and
  `GET /bff/agora/trading-room/decision-events`; all three carried
  authorization and returned HTTP 200.

Screenshots:

- [full Performance tab](./AG-UIPOL-004-performance-page.png)
- [performance-table close-up](./AG-UIPOL-004-performance-table.png)

## Machine-readable readback and checksums

[AG-UIPOL-004-readback.json](./AG-UIPOL-004-readback.json) records the exact
FE/BFF identities, run conclusions, snapshot/card identifiers, readiness
classes, row order, metric states, and observed network provenance. It stores
only whether authorization was present; it does not store a bearer token.

Artifact SHA-256:

- `dd7c73e087772b1c9a6bdc24714b6d3cb1acc4a50f0c61a33e06b3b3cd1b356e`
  — full workshop screenshot
- `abf9fd25025dd86b29eedaf19b90f0ef6e00276066cae441d9ccda597d767624`
  — rail close-up
- `1e544fe979afa68cb557427b09704c39046dd23f84678a38fa35b47017546033`
  — completeness-card close-up
- `3d4c7914f1310f8fb9c75d6481860042260632b2d4f6289bedb0356a6a33cf79`
  — full Performance screenshot
- `92978bd88f0ef1e74018b364debb7bbe064fc8a3115f00fbfb24801002c3af4a`
  — performance-table close-up
- `f279930d3b0e0dbdaa8d0333d698acf3e9d78b17df8358baac9fe770c9758c68`
  — readback JSON

## Validation and residuals

- Focused Vitest at current frontend `936f252e...`: four files, 54/54 tests
  passed. Existing React `act(...)` warnings remained non-failing.
- Hosted standalone-shell Playwright probe: Workshop and Performance desktop
  routes passed 2/2; the task-specific authenticated capture then asserted the
  values and network provenance above.
- `jq empty` and `sha256sum` validate the checked-in readback and artifacts.
- Workshop EventSource still targets the FE-origin `/bff/.../stream`, receives
  HTML 200, and reports the known `text/html` versus `text/event-stream` MIME
  error. The task's authenticated REST reads and visible state are unaffected;
  this record does not claim the SSE routing defect is fixed.
- The runtime workshop response still omits canonical `subject`. PR #335 makes
  the current UI safe for that real payload, but the BFF projection/schema
  drift remains a separate contract-hardening follow-up rather than a hidden
  AG-UIPOL-004 closure claim.
