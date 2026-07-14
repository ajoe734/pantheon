# AG-UIPOL-007 hosted evidence

Captured: 2026-07-14 06:18:57 UTC

This record pins the reviewer-approved multi-lens Trading Room delivery to an
accepted Pantheon dev deployment and records the task owner's desktop, narrow,
contract-regression, provenance, accessibility, and authority-boundary checks.

## Accepted deployment

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `execute-plans@dev`: `36a2f9292eadccd32b1fd79db2e7820ce750a984`
- Pantheon BFF recorded by the manifest:
  `4a27eb31fcb35c10cfb1519475a596b81e908e20`
- Manifest deployment time: `20260714T061341Z`
- Successful atomic deploy and read-only browser/BFF probe:
  [GitHub Actions run 29310638792](https://github.com/ajoe734/execute-plans/actions/runs/29310638792)

The hosted `deployment.json` exactly matched the frontend merge SHA and
reported these safe build settings:

| Setting | Accepted value |
|---|---|
| `VITE_BFF_MODE` | `live` |
| `VITE_BFF_FALLBACK` | `strict` |
| `VITE_BFF_REAL_WRITES` | `false` |
| `VITE_BFF_ALLOW_DEV_STUB_WRITES` | `false` |
| `VITE_BFF_EMBEDDED_BEARER_TOKEN` | `false` |

## Delivered revisions

- execute-plans [PR #319](https://github.com/ajoe734/execute-plans/pull/319),
  merge `26cef514493d8d1cbb7137d240a66fda1b02a8b1`: five Strategy
  Lenses, dashboard recipes, candidate board, and review drawer.
- execute-plans [PR #320](https://github.com/ajoe734/execute-plans/pull/320),
  merge `2fb8b36e17b9d0de80c036045c841dcbdb02cc9b`: i18n, Candidate
  Pool integration boundary, sample disclosure, dynamic workspace handoff,
  and drawer keyboard/focus behavior.
- execute-plans [PR #322](https://github.com/ajoe734/execute-plans/pull/322),
  merge `3ce439d8713dcb437673bf7b81df78cb917d8082`: review findings and
  composition with the current AG-UIPOL-008 Winner Branch workspace.
- execute-plans [PR #341](https://github.com/ajoe734/execute-plans/pull/341),
  merge `36a2f9292eadccd32b1fd79db2e7820ce750a984`: canonical
  `full | partial | missing` availability normalization for proposal,
  workspace, version, and revision response graphs plus render-safe fallback.

PR #341 merged only after both Branch CI event sets passed commit trailers,
generated-file guard, deploy-safety contract, and production build checks.

## Hosted browser acceptance

The task-specific Playwright run used Chromium at `1600x1100` for desktop and
`768x1200` for the narrow interaction proof. It supplied `--disable-quic` to
remove a known runner transport-switching flake from the evidence pass. The
official deploy workflow's independent browser/BFF probe also passed without a
task-specific probe override.

### Canonical availability regression

The root Trading Room selected a live ready strategy and created a governed
workspace proposal. `workspace-proposal-preview` and
`workspace-proposal-data-availability` rendered successfully with the visible
state `工作區資料可用性 / 部分可用`. There were zero page errors and zero console
errors; the former `Cannot read properties of undefined (reading 'bg')` crash
was absent.

### Five distinct lenses

Each selected card exposed the gold selected border, deselected the concrete
strategy workspace, rendered its own recipe, kept the recipe's sample-only
badge visible, and rendered a lens-specific candidate column.

| Lens | Recipe proof | Candidate-column proof | Candidate provenance |
|---|---|---|---|
| A — large-holder accumulation | `dashboard-recipe-a`; Candidate Funnel & Flow | Accum. Days | explicit sample fallback |
| B — industry laggard | `dashboard-recipe-b`; Active Hypothesis | Peer Group | explicit sample fallback |
| C — technical breakout | `dashboard-recipe-c`; Breakout Resistance Levels | Breakout Level | explicit sample fallback |
| D — event trading | `dashboard-recipe-d`; Expectation Gap Scenario Tree | Event Type | explicit sample fallback |
| E — large-flow/liquidity | `dashboard-recipe-e`; Execution Slippage Model | Target Amount | explicit sample fallback |

No successful Candidate Pool member response was observed during this pass.
All five surfaces therefore displayed the amber sample-data warning and are
classified as explicit fallback, not live candidate data. The presentation
lens identifiers remain unbound to a canonical Candidate Pool identity; that
governed mapping is residual work and is not concealed by this closeout.

### Narrow board and drawer accessibility

- The `768px` capture shows all five switcher cards and the strategy row.
- The narrow candidate-board capture preserves the dense, horizontally
  constrained table and the active lens label.
- Keyboard focus was placed on the first candidate row (`AAPL`); `Enter`
  opened a `role=dialog`, `aria-modal=true` review drawer.
- Initial focus landed on close. `Shift+Tab` wrapped to the final Winner Branch
  action, and `Tab` wrapped back to close.
- All monitor, shadow, research, park, exclude, and Winner Branch actions were
  visible but were not activated.
- `Escape` closed the drawer and restored focus to the originating candidate
  row.

### Network and authority boundary

Observed successful live responses were the Trading Room aggregate `GET 200`,
decision-events `GET 200`, event stream `GET 200`, and one governed workspace
proposal `POST 201`:

`/bff/agora/strategies/full003-live-1783268175-13279b/trading-room/proposals`

The proposal creation is recorded instead of claiming a read-only browser
session. There were no failed BFF requests and no order, broker, capital,
runtime-binding, or execution-plane requests. No candidate lifecycle action
was clicked or claimed as persisted.

## Evidence artifacts

The machine-readable assertion record is
[`AG-UIPOL-007-hosted-smoke.json`](./AG-UIPOL-007-hosted-smoke.json).

| Artifact | Dimensions | SHA-256 |
|---|---:|---|
| [`AG-UIPOL-007-desktop-lens-a.png`](./AG-UIPOL-007-desktop-lens-a.png) | 1600x1100 | `b938e817750d8911be1e3fba37e05cb4760b2ebfea7d5be4d688b29f3ee1222e` |
| [`AG-UIPOL-007-desktop-lens-b.png`](./AG-UIPOL-007-desktop-lens-b.png) | 1600x1100 | `eac2e751b6c659e1a9b8af0a0d982e7876a0b36d7b792191c388d4aee8f3819c` |
| [`AG-UIPOL-007-desktop-lens-c.png`](./AG-UIPOL-007-desktop-lens-c.png) | 1600x1100 | `edb24e6a8c00285ff57825ed1418102fab6868a89987d3bfc0660b6c6e0f4fcc` |
| [`AG-UIPOL-007-desktop-lens-d.png`](./AG-UIPOL-007-desktop-lens-d.png) | 1600x1100 | `c5f942e8aa62b01dd625f3a1e1fa1b08b39ae52de565c63f539fdef6854922c4` |
| [`AG-UIPOL-007-desktop-lens-e.png`](./AG-UIPOL-007-desktop-lens-e.png) | 1600x1100 | `aba2c43cf073f5e321f8968a6b8c17ed462054d1cdf6ff59503df41252a19339` |
| [`AG-UIPOL-007-narrow-switcher.png`](./AG-UIPOL-007-narrow-switcher.png) | 768x265 | `b301fede65baef005cfbba393a83ba372171e6ff501eba95c347279f30e7dbc1` |
| [`AG-UIPOL-007-narrow-board.png`](./AG-UIPOL-007-narrow-board.png) | 528x260 | `aba2f331564966975113d6c6b9c904890b44c4d997b86a57bddab17f9b78807a` |
| [`AG-UIPOL-007-narrow-drawer.png`](./AG-UIPOL-007-narrow-drawer.png) | 768x1200 | `1166a3c4aa78c9727d49afdd0b4d67be8efb29a4dc7e0767598fe872ab07d281` |

## Local validation

The final availability compatibility change passed:

```text
npx vitest run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/trading-room/TradingRoomPage.test.tsx
# 121/121 passed

npx tsc --noEmit
# passed

npx eslint src/agora/trading-room/WorkspaceProposalPreview.tsx src/lib/bff-v1/agora/tradingRoom.ts src/lib/bff-v1/agora/tradingRoom.test.ts
# 0 errors, 0 warnings

git diff --check
# passed

VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=false \
VITE_BFF_ALLOW_DEV_STUB_WRITES=false \
npm run build
# passed
```

## Residual truth boundaries

- All dashboard recipe bodies are labelled sample-only and must not be read as
  live telemetry.
- Candidate rows in this capture are explicit sample fallback. A canonical
  lens-to-pool identity and governed persisted review transitions remain
  future work.
- Drawer lifecycle controls update this surface's visible state only; this task
  does not claim a Candidate Pool mutation.
- AG-UIPOL-008 retains Winner Branch workspace ownership, and AG-UIPOL-011
  retains the final cross-surface responsive gate.
- `parity-matrix.md` remains the AG-UIPOL-005 pre-delivery audit baseline. This
  task closeout does not silently rewrite its TR-01–TR-09 verdicts.
