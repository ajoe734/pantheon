# AG-UIPOL-004 Review — Codex

Reviewer: Codex  
Owner: Antigravity  
Review date: 2026-07-14 UTC

## Scope reviewed

- `ajoe734/execute-plans` PRs #290 and #295, merged to `dev` as
  `484d0779ea21d250ab9879a0bd5ec7742d11a328` and
  `12b78ef210e535cd4a3d80358f78b44c9396e588`.
- Corrective `ajoe734/execute-plans` PR #325, merged after the recorded
  screenshots as `2fdf639afc6e48fa41eaaaa1fef6a2034dbfd3e3`.
- Pantheon delivery/evidence PRs #3567 and #3590.
- The hosted evidence and machine-readable readback under
  `docs/bff/execution-tasks/2026-07-13-agora-ui-polish/evidence/`.
- The Pantheon-owned FE manifest and hosted FE trees
  `ca5f0942b2ee7d96978975890e67704e3830b66e` and successor
  `b5d64856c9be1caa32078253a9f3758ed5abe07c`. Both contain corrective PR #325;
  the successor changes only two unrelated Evolution Journal files.

## Code-level and historical functional acceptance

The objective UI behavior is covered in the implementation and in the
historical screenshots captured at pre-correction commit `12b78ef...`:

- the rail and completeness card use a matching snapshot/card identity and
  report `Complete`, `100%`, and research-ready `Yes` together;
- Ready gates render as active states;
- named strategies sort before the explained `Unassigned` attribution bucket;
- measured zero (`$0`) is distinct from absent/unreported values;
- the evidence screenshots visibly show those states.

Focused verification at the currently hosted FE commit:

```text
npm test -- \
  src/lib/bff-v1/agora/workshops.test.ts \
  src/agora/components/StrategyCompletenessRail.test.tsx \
  src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx \
  src/agora/pages/strategy-performance/StrategyPerformancePage.test.tsx

Test Files  4 passed (4)
Tests       51 passed (51)
```

The current manifest reports the required safe frontend defaults:
`VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
`VITE_BFF_REAL_WRITES=false`, and
`VITE_BFF_ALLOW_DEV_STUB_WRITES=false`.

These facts are not sufficient for hosted acceptance because the current
deployment has the blockers below.

## Blocking finding — current hosted Agora regressed after the screenshots

The delivery record still calls PRs #290/#295 and `12b78ef...` final, but
corrective PR #325 changed the task implementation after those screenshots and
is included in the currently hosted FE. There is no hosted screenshot or
readback proving the post-#325 tree.

A fresh headless Chromium probe against the current hosted deployment found:

- the exact workshop route completes its workshop/readiness/events/
  completeness/cards BFF reads with HTTP 200, then falls into the UI error
  boundary with `TypeError: Cannot read properties of undefined (reading
  'kind')`;
- the current BFF workshop payload omits `subject`, while the hosted source at
  `StrategyWorkshopPage.tsx:645` dereferences `workshop?.subject.kind` rather
  than `workshop?.subject?.kind`;
- `/agora/strategy-performance` remains on its loading message after 25 seconds
  and emits no trading-room or performance-attribution BFF request.

Therefore the current hosted Workshop and Performance surfaces do not satisfy
the task acceptance even though the focused component tests pass.

## Blocking finding — the hosted BFF identity is fabricated, not deployed

The current FE `/deployment.json` reports:

```text
bffCommit=27cd46529c29801db02818aafe4df723cc0f8666
```

That object does not exist in `ajoe734/pantheon`; both local object lookup and
the GitHub commits API reject it. The real Pantheon `dev` merge with the same
short prefix is:

```text
27cd4652995a53089c77e7c3613bf0cd955971f4
```

The values diverge immediately after `27cd46529`. The bad value is not a
readback typo: `scripts/deploy-dev-vm.sh` at the hosted FE commit hardcodes the
nonexistent SHA as the default `PANTHEON_DEPLOY_BFF_COMMIT` value. The FE deploy
run `29299500683` succeeded and published that false identity.

The real Pantheon candidate is not proven hosted either. Pantheon Nonprod
Deploy run `29299038637` targeted
`27cd4652995a53089c77e7c3613bf0cd955971f4` and failed. The BFF
`/deployment.json` endpoint returns 404, so it cannot independently repair the
false FE manifest claim. The live BFF's `/bff/version` endpoint identifies the
served source as `7475a06873202970dc6a827e4645430b192a536a`, which directly
contradicts the FE manifest.

Pantheon's dev-hosting gate requires exact FE and BFF identities and treats a
candidate whose deployment failed as unaccepted. Therefore the task cannot be
approved while its status message claims `bffCommit deployed`.

## Blocking finding — the evidence provenance and checksum are invalid

Pantheon commit `a8ba4196e` added the performance-attribution network event to
`AG-UIPOL-004-readback.json` but did not update the checksum recorded in the
hosted-evidence document. The document claims
`113886e1fb29430576944b9045fef28596d6fa308d400b3d09ce8839126f51a4`;
the checked-in JSON now hashes to
`1ca4a070d2ef08c38fd7e9f4a85587be09c08b52871ee843e44a388d8442a761`.

The same edit claims the browser observed HTTP 200 from
`/bff/agora/trading-room/performance-attribution`. The exact recorded FE source
at `12b78ef...` calls
`/bff/management/performance-attribution/by-strategy` instead, and the claimed
Agora route currently returns HTTP 404. The evidence bundle is therefore
neither self-verifying nor valid request provenance in its current state.

## Required changes

1. Repair the current Workshop `subject` dereference and Performance loading
   failure, then add regression coverage for the real hosted payload shape.
2. Remove the fabricated/default BFF SHA from FE deployment-manifest
   generation. Supply the BFF identity from verified deployment truth and fail
   the manifest/deploy gate when that identity cannot be proven.
3. Repair the Pantheon dev BFF deployment failure and successfully deploy the
   intended BFF commit.
4. Redeploy the FE manifest with safe write defaults and the exact verified BFF
   commit, then prove that both recorded commits exist and are the revisions
   actually served.
5. Refresh AG-UIPOL-004 delivery record, hosted screenshots, readback, and
   checksums against the post-correction accepted deployment. Record the exact
   FE/BFF identities without claiming a failed or nonexistent BFF candidate.

## Verdict

**Changes requested — reopen to Antigravity.** Focused tests pass, but the
current hosted Workshop crashes, Performance never leaves loading, the FE
manifest's BFF identity is false, the real BFF candidate's deploy failed, and
the readback provenance/checksum are invalid.

LLM-Agent: Codex  
Task-ID: AG-UIPOL-004  
Reviewer: Antigravity  
Verified: 4 Vitest files / 51 tests; historical screenshots; current headless
Chromium probe; FE manifest; live BFF `/bff/version`; git and GitHub SHA lookup;
artifact SHA-256; FE deploy runs 29299500683 and 29299854068; BFF deploy run
29299038637
