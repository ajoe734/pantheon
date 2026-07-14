# AG-UIPOL-004 hosted evidence

Captured: 2026-07-13 13:18:18 UTC

This record proves the objective state-consistency defects in
`AG-UIPOL-004`; it does not claim design parity.

## Delivered revisions

- execute-plans PR [#290](https://github.com/ajoe734/execute-plans/pull/290)
  introduced the readiness styling and performance-table semantics from task
  commit `cd118f95a459fe1a4a6f5e37a5f2bb1e016027ea`; merge commit
  `484d0779ea21d250ab9879a0bd5ec7742d11a328`.
- execute-plans PR [#295](https://github.com/ajoe734/execute-plans/pull/295)
  aligned the rail with the exact raw hosted completeness snapshot from task
  commit `f224267148a6f3ccb9ede3f4e8321a2bca345b6e`; PR head
  `c6e820eec21a827c45955f6f1e5881129de9afa6`; merge commit
  `12b78ef210e535cd4a3d80358f78b44c9396e588`.
- Required post-merge Branch CI run
  [29252591748](https://github.com/ajoe734/execute-plans/actions/runs/29252591748)
  passed all three jobs (`Commit trailers`, `Generated files guard`, and
  `Smoke acceptance`) for `12b78ef210e535cd4a3d80358f78b44c9396e588`.
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
  reported app `execute-plans`, source branch `dev`, live/strict BFF mode, and
  exact commit `12b78ef210e535cd4a3d80358f78b44c9396e588` before capture.

The automatic dev deploy run
[29252591717](https://github.com/ajoe734/execute-plans/actions/runs/29252591717)
did publish that exact SHA, but its first two attempts ended red in the
unrelated generic `/management/persona-fleet` post-deploy probe. Attempt 1
timed out while a linked runtime page remained loading; attempt 2 encountered
`ERR_NETWORK_CHANGED` and could not fetch an existing `PlatformShell` chunk,
so it emitted no BFF requests. The third rerun attempt was cancelled. Thus,
the exact-SHA deploy attempts ended in failure, failure, and cancelled status.
This anchor records the successful hosted task proof and notes the terminal failures of these deploy attempts.

## Workshop state-consistency proof

The browser loaded this real hosted route without response interception:

`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/strategy-workshop/b888fb96-12b4-46e1-8def-ffe4f29b5ad7`

The readback manifest ties all visible values to workshop
`b888fb96-12b4-46e1-8def-ffe4f29b5ad7`, snapshot
`8f7dc9e4-108f-4067-8d05-9cad30c7e17a`, strategy version
`full003-postdeploy-1783268578-f4b6f0-v1`, and completeness card
`card_completeness_8f7dc9e4-108f-4067-8d05-9cad30c7e17a`.

Assertions:

- the completeness card says `complete` and `Research ready: Yes`;
- the rail says `Complete`, `100%`, and `Research ready: Yes`;
- all seven rail dimensions render `Complete`;
- Preliminary research, Full validation, and Trading room render as active
  green Ready gates rather than disabled labels;
- the browser observed HTTP 200 for the workshop, events, completeness,
  readiness, and cards BFF reads.

Screenshots:

- [full workshop consistency](./AG-UIPOL-004-workshop-consistency.png)
- [rail close-up](./AG-UIPOL-004-workshop-rail.png)
- [completeness-card close-up](./AG-UIPOL-004-workshop-completeness-card.png)

## Performance semantics proof

The browser loaded the real hosted
`/agora/strategy-performance` route against the same deployed SHA.

Assertions:

- two named strategies render before the aggregate `Unassigned` row;
- named strategy metrics with no measurement render `not reported`;
- the measured aggregate value renders `$0` and is not conflated with
  `not reported`;
- `Unassigned` explains that it aggregates telemetry the BFF could not link
  to a named Trading Room strategy;
- `Unassigned` is the third row (`unassignedIndex: 2`) and is labeled
  `attribution only`.

Screenshots:

- [full Performance tab](./AG-UIPOL-004-performance-page.png)
- [performance-table close-up](./AG-UIPOL-004-performance-table.png)

## Machine-readable readback

[AG-UIPOL-004-readback.json](./AG-UIPOL-004-readback.json) records the exact
deployment identity, snapshot/card identifiers, displayed values, table row
order, and observed network events.

Artifact SHA-256:

- `9bf8a76bfea35c2bba1720d0b3ab3b4b06ccbe2169c50c4f4e6dd61f35331e6d`
  — full workshop screenshot
- `97f694f104124d662d7649011a9aa4678619f784f177c5644cfb71699e4bfc34`
  — rail close-up
- `86ebd24dbd4c30a0da8d0c2453c1827062cf5931d6100b02b5ab3e62f8d90d8c`
  — completeness-card close-up
- `1a1e700eddc205c4102f723c8f6a2735da6d1a6c6f70209ab39e43e3b5c7e522`
  — full Performance screenshot
- `40ad3e13c555b99d170bb9f2a5208c411a47c813d9d136a66306358769e20844`
  — performance-table close-up
- `113886e1fb29430576944b9045fef28596d6fa308d400b3d09ce8839126f51a4`
  — readback JSON

## Validation and residuals

- `npm test -- src/lib/bff-v1/agora/workshops.test.ts src/agora/components/StrategyCompletenessRail.test.tsx src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx`
  -> 44/44 passed.
- `npx tsc --noEmit` -> passed.
- scoped ESLint over the seven PR #295 source/test files -> passed.
- `npm run build` -> passed with pre-existing bundle/CSS warnings.
- The hosted readback observed one pre-existing EventSource MIME warning for
  the workshop stream (`text/html` instead of `text/event-stream`). The
  required REST reads and the task-specific assertions still passed; this
  record does not claim that the SSE warning was repaired by AG-UIPOL-004.
- The full post-merge integration run
  [29252591712](https://github.com/ajoe734/execute-plans/actions/runs/29252591712)
  remained red on broader Trade Journeys, Persona Fleet, Winner Branch, and
  focus-handling scenarios. Those failures are recorded as residual suite
  state and are not represented as green AG-UIPOL-004 evidence.
