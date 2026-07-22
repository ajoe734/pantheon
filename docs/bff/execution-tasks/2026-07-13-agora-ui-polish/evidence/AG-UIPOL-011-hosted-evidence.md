# AG-UIPOL-011 hosted evidence

Captured: 2026-07-15 06:21:00–06:27:00 UTC (re-verification round after the
2026-07-14 review found the previously committed evidence stale and
task/evidence/matrix truth out of sync)

This record pins the reviewer-approved narrow responsive Trading Desk delivery to an accepted Pantheon dev deployment and records the task owner's responsive, container-regression, accessibility, and authority-boundary checks across phone-390, tablet-768, desktop-1280, and wide-2560 viewports.

## Accepted deployment

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `execute-plans@dev`: `79e0f8f3083c8546ec2c139afbc339322dcbe755`
- Pantheon BFF recorded by the manifest:
  `a10f752b3ea4420f271535e255f2d4e7d3d498b2`
- Manifest deployment time: `20260715T054747Z`
- Successful atomic deploy and read-only browser/BFF probe:
  [GitHub Actions run 29392291433](https://github.com/ajoe734/execute-plans/actions/runs/29392291433) (Pantheon Dev FE Deploy, push-triggered, `sourceBranch: dev` verified)

### Known follow-up: not pinned to the literal current dev HEAD

`execute-plans@dev` has advanced past `79e0f8f3083c` (to `288fd70d9` as of this
writing) with commits unrelated to AG-UIPOL-011 (EVOCHAIN-009, PINT-010-R2).
A fresh hosted run against that newer deployment was attempted and failed —
not on any Agora UI assertion, but because a manual `workflow_dispatch` of
"Pantheon Dev FE Deploy" was triggered with `ref=<full SHA>` instead of the
default `ref=dev`, which propagates into `deployment.json` as
`sourceBranch: "<sha>"` instead of the literal string `"dev"`. The hosted
spec's `deploymentGate()` step asserts `deployment.sourceBranch === "dev"`
and correctly fails closed on that mismatch — this is a deploy-pipeline
provenance regression, not a product defect, and it cannot be re-triggered
from this task without a human re-running the deploy workflow with the
default `ref` (or waiting for the next ordinary `dev` push, which redeploys
correctly on its own). The evidence below is the last cleanly-passing,
correctly-provenanced hosted run and reflects real, current Agora UI
behavior; only the deploy metadata on the very latest dev commit is
temporarily broken.

The hosted `deployment.json` exactly matched the frontend merge SHA and reported these safe build settings:

| Setting | Accepted value |
|---|---|
| `VITE_BFF_MODE` | `live` |
| `VITE_BFF_FALLBACK` | `strict` |
| `VITE_BFF_REAL_WRITES` | `false` |
| `VITE_BFF_ALLOW_DEV_STUB_WRITES` | `false` |
| `VITE_BFF_EMBEDDED_BEARER_TOKEN` | `false` |

## Delivered revisions

- execute-plans [PR #344](https://github.com/ajoe734/execute-plans/pull/344), merge `cbc6877630e0af087cd4d119da6024d816e4e495`: narrow responsive parity implementation across all tabs and drawers (rows G-06, PF-07, and SRV-03).
- execute-plans [PR #345](https://github.com/ajoe734/execute-plans/pull/345), merge `b6a5bc9311941cf7333c5f738526868715533101`: harden hosted drawer gate.
- execute-plans [PR #346](https://github.com/ajoe734/execute-plans/pull/346), merge `cb139ca8a4e4a1236033b2ccbdd917907de592cc`: harden hosted drawer gate test and verification.

## Hosted browser acceptance

The task-specific Playwright run checked four viewports:
- **phone-390** (390x844)
- **tablet-768** (768x1024)
- **desktop-1280** (1280x900)
- **wide-2560** (2560x1440)

For each viewport, the following steps were validated by live Playwright
assertions against the deployed frontend and live BFF — no route
interception (`page.route`) is used anywhere in the spec, so every check
below observed real, non-mocked network responses:
1. **Trading Room**: Exposes task/decision/risk first on narrow screens.
   On narrow viewports the Servant drawer opens as a full-viewport-width
   `role="dialog"` overlay, sets `inert` on the background content behind
   it, keeps its close action reachable inside the viewport, restores
   focus to the trigger button on `Escape`, and the same focus-trap /
   inert-background / trigger-restoration sequence is separately asserted
   for the Candidate Review drawer. **Proof these checks passed:** the
   spec asserts each of these conditions inline (`toHaveAttribute("role",
   "dialog")`, `toHaveAttribute("inert", "")`, `toBeFocused()`, etc.) and
   only reaches the final `writeFileSync` of the per-viewport JSON
   readback (see below) after every prior assertion in that test has
   succeeded — a failed drawer/focus assertion would abort the test before
   the readback file is ever written, so the readback file's presence for
   all four viewports **is** the pass record for these checks. Screenshots
   are taken after each drawer is closed via `Escape`, so they show
   contained closed-state layout, not the open-drawer moment — the
   accessibility proof for the open state is the assertions, not a
   screenshot.
2. **Strategy Workshop**: The desktop three-column layout switches cleanly to an interactive selector rail on mobile, showing either the Conversation/Composer or the completeness-rail without horizontal page overflow.
3. **Performance**: Desktop table layout switches to narrow responsive card comparison stacks on mobile.

All viewports proved zero page body horizontal scroll/overflow, and strict alignment to the safe viewport boundaries (see the `budgets` object in each JSON readback for the raw measured values).

## Evidence artifacts

The machine-readable assertion records — each written only after every
Playwright assertion for that viewport (containment budgets, deployment
gate, Servant/Candidate drawer focus-trap/Escape/inert/trigger-restoration
checks) passed — are:
- [`ag-uipol-011-79e0f8f3083c-phone-390.json`](./ag-uipol-011-79e0f8f3083c-phone-390.json)
- [`ag-uipol-011-79e0f8f3083c-tablet-768.json`](./ag-uipol-011-79e0f8f3083c-tablet-768.json)
- [`ag-uipol-011-79e0f8f3083c-desktop-1280.json`](./ag-uipol-011-79e0f8f3083c-desktop-1280.json)
- [`ag-uipol-011-79e0f8f3083c-wide-2560.json`](./ag-uipol-011-79e0f8f3083c-wide-2560.json)

| Artifact | Dimensions | SHA-256 |
|---|---:|---|
| [`ag-uipol-011-79e0f8f3083c-phone-390-trading-room.png`](./ag-uipol-011-79e0f8f3083c-phone-390-trading-room.png) | 390x844 | `7592976eaa1d43b64650bcf8f0b3ca9234484dedabadbb5e931790a9d6d741d3` |
| [`ag-uipol-011-79e0f8f3083c-phone-390-strategy-workshop.png`](./ag-uipol-011-79e0f8f3083c-phone-390-strategy-workshop.png) | 390x844 | `eea53fd35e2a1fe25c3e00b41861df67cb57a82ba939671eed7bee690d85fe95` |
| [`ag-uipol-011-79e0f8f3083c-phone-390-strategy-performance.png`](./ag-uipol-011-79e0f8f3083c-phone-390-strategy-performance.png) | 390x844 | `a2723d6a32fe09a988ba0a429681396b1721431e6f53de2b817fa5381f9a22af` |
| [`ag-uipol-011-79e0f8f3083c-tablet-768-trading-room.png`](./ag-uipol-011-79e0f8f3083c-tablet-768-trading-room.png) | 768x1024 | `8a5ec3bdae32091302940a97383a41c0746759e284e70473a2007910ecbb56fd` |
| [`ag-uipol-011-79e0f8f3083c-tablet-768-strategy-workshop.png`](./ag-uipol-011-79e0f8f3083c-tablet-768-strategy-workshop.png) | 768x1024 | `5017760571dd3d4318b81335b49d209de63eefa3ab9be6bc8b198739b063be83` |
| [`ag-uipol-011-79e0f8f3083c-tablet-768-strategy-performance.png`](./ag-uipol-011-79e0f8f3083c-tablet-768-strategy-performance.png) | 768x1024 | `bfbed63bae0bd83cd5289707f25ddb33d503b5950e29aecaa1a75c50bfee9a58` |
| [`ag-uipol-011-79e0f8f3083c-desktop-1280-trading-room.png`](./ag-uipol-011-79e0f8f3083c-desktop-1280-trading-room.png) | 1280x900 | `e6d780cad244e324e641f2847464001a27822c6154d2f84c16964c7dcef0e94b` |
| [`ag-uipol-011-79e0f8f3083c-desktop-1280-strategy-workshop.png`](./ag-uipol-011-79e0f8f3083c-desktop-1280-strategy-workshop.png) | 1280x900 | `6d139f4bba55059b5f555bda8b0ddccd9a5fd408042b515f73c3358450b93561` |
| [`ag-uipol-011-79e0f8f3083c-desktop-1280-strategy-performance.png`](./ag-uipol-011-79e0f8f3083c-desktop-1280-strategy-performance.png) | 1280x900 | `a54f5cdbc0496b01420514b6797a0f07537e843d05d58d78efc55ee30d9e30bf` |
| [`ag-uipol-011-79e0f8f3083c-wide-2560-trading-room.png`](./ag-uipol-011-79e0f8f3083c-wide-2560-trading-room.png) | 2560x1440 | `9adf87e172bf7f0d539a550a02df8dc4b282832c3946d091c264b0b416f14b8e` |
| [`ag-uipol-011-79e0f8f3083c-wide-2560-strategy-workshop.png`](./ag-uipol-011-79e0f8f3083c-wide-2560-strategy-workshop.png) | 2560x1440 | `3c3963882514d16ac7a9a7ef80e570e9cc241e53d7a7f18c7ab60bfb10960147` |
| [`ag-uipol-011-79e0f8f3083c-wide-2560-strategy-performance.png`](./ag-uipol-011-79e0f8f3083c-wide-2560-strategy-performance.png) | 2560x1440 | `64504e5cfe1594d99e8f082f854f4a2a324d9731d1814e8ee3f25705fc6dca17` |
| [`ag-uipol-011-79e0f8f3083c-phone-390.json`](./ag-uipol-011-79e0f8f3083c-phone-390.json) | readback | `96f1b0122c94d3afe7f62420819ce643807aa525547839fe0bf693a744206abf` |
| [`ag-uipol-011-79e0f8f3083c-tablet-768.json`](./ag-uipol-011-79e0f8f3083c-tablet-768.json) | readback | `4ae0c62e125d93522f603f3e809933f1fa6e29b1ff60c051591c25bdf80aeb2f` |
| [`ag-uipol-011-79e0f8f3083c-desktop-1280.json`](./ag-uipol-011-79e0f8f3083c-desktop-1280.json) | readback | `df9bdda642166d9fc58d9d727619d70531a3a3fa4c057af35b2140f0ea29c722` |
| [`ag-uipol-011-79e0f8f3083c-wide-2560.json`](./ag-uipol-011-79e0f8f3083c-wide-2560.json) | readback | `ecbf8aac4b5594dfea58e6baff954e14fbd71496b54f8d16015eb000d9ec169d` |

Note: the four `*-trading-room.png` screenshots are byte-identical to the
previously committed `b6a5bc931194`-prefixed captures (same SHA-256) because
the Trading Room surface did not change between those two deployments; the
`*-strategy-workshop.png` and `*-strategy-performance.png` captures differ
because they reflect live (non-mocked) BFF data that had advanced between
the two capture sessions — expected for a "live, not intercepted" evidence
gate, not a regression.

## Residual truth boundaries

- The narrow layouts collapse controls dynamically but do not change the underlying desktop grid coordinates or default workspace database structure.
- Modal/drawer transitions use focused accessibility gates; no mutation side-effects are claimed on live portfolios.
- `parity-matrix.md` rows G-06, PF-07, and SRV-03 are updated to reflect the verified narrow behavior.
