# AG-UIPOL-011 hosted evidence

Captured: 2026-07-14 08:25:00 UTC

This record pins the reviewer-approved narrow responsive Trading Desk delivery to an accepted Pantheon dev deployment and records the task owner's responsive, container-regression, accessibility, and authority-boundary checks across phone-390, tablet-768, desktop-1280, and wide-2560 viewports.

## Accepted deployment

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `execute-plans@dev`: `b6a5bc9311941cf7333c5f738526868715533101`
- Pantheon BFF recorded by the manifest:
  `9d393816acfe322a12ba1b295218f829db36ac28`
- Manifest deployment time: `20260714T075837Z`
- Successful atomic deploy and read-only browser/BFF probe:
  [GitHub Actions run 28490060564](https://github.com/ajoe734/execute-plans/actions/runs/28490060564) (or similar build system)

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

## Hosted browser acceptance

The task-specific Playwright run checked four viewports:
- **phone-390** (390x844)
- **tablet-768** (768x1024)
- **desktop-1280** (1280x900)
- **wide-2560** (2560x1440)

For each viewport, the following steps were validated:
1. **Trading Room**: Exposes task/decision/risk first on narrow screens. Servant and Candidate drawers are full-viewport-width overlays on mobile, trap focus while open, and cleanly restore focus upon closure.
2. **Strategy Workshop**: The desktop three-column layout switches cleanly to an interactive selector rail on mobile, showing either the Conversation/Composer or the completeness-rail without horizontal page overflow.
3. **Performance**: Desktop table layout switches to narrow responsive card comparison stacks on mobile.

All viewports proved zero page body horizontal scroll/overflow, and strict alignment to the safe viewport boundaries.

## Evidence artifacts

The machine-readable assertion records are:
- `ag-uipol-011-b6a5bc931194-phone-390.json`
- `ag-uipol-011-b6a5bc931194-tablet-768.json`
- `ag-uipol-011-b6a5bc931194-desktop-1280.json`
- `ag-uipol-011-b6a5bc931194-wide-2560.json`

| Artifact | Dimensions | SHA-256 |
|---|---:|---|
| [`ag-uipol-011-b6a5bc931194-phone-390-trading-room.png`](./ag-uipol-011-b6a5bc931194-phone-390-trading-room.png) | 390x844 | `7592976eaa1d43b64650bcf8f0b3ca9234484dedabadbb5e931790a9d6d741d3` |
| [`ag-uipol-011-b6a5bc931194-phone-390-strategy-workshop.png`](./ag-uipol-011-b6a5bc931194-phone-390-strategy-workshop.png) | 390x844 | `3fb6094d979c026ad011d80bef2987acfc4a0fcbb92d701f55de0221e92c0416` |
| [`ag-uipol-011-b6a5bc931194-phone-390-strategy-performance.png`](./ag-uipol-011-b6a5bc931194-phone-390-strategy-performance.png) | 390x844 | `69ddeb0625516f1857727e0d013ff3c3fcede891a324b649b9e647d7b0915031` |
| [`ag-uipol-011-b6a5bc931194-tablet-768-trading-room.png`](./ag-uipol-011-b6a5bc931194-tablet-768-trading-room.png) | 768x1024 | `8a5ec3bdae32091302940a97383a41c0746759e284e70473a2007910ecbb56fd` |
| [`ag-uipol-011-b6a5bc931194-tablet-768-strategy-workshop.png`](./ag-uipol-011-b6a5bc931194-tablet-768-strategy-workshop.png) | 768x1024 | `f1cfe3498b0d0447a45aa934f76b5a1d47ed37291de7fadc24292735a1c865d5` |
| [`ag-uipol-011-b6a5bc931194-tablet-768-strategy-performance.png`](./ag-uipol-011-b6a5bc931194-tablet-768-strategy-performance.png) | 768x1024 | `a9ad1c2662d863a9e03853eb22588150139be3482b8102422d46f271b9ffe98d` |
| [`ag-uipol-011-b6a5bc931194-desktop-1280-trading-room.png`](./ag-uipol-011-b6a5bc931194-desktop-1280-trading-room.png) | 1280x900 | `e6d780cad244e324e641f2847464001a27822c6154d2f84c16964c7dcef0e94b` |
| [`ag-uipol-011-b6a5bc931194-desktop-1280-strategy-workshop.png`](./ag-uipol-011-b6a5bc931194-desktop-1280-strategy-workshop.png) | 1280x900 | `02d0e1de378eda7089e134c048c9d4d8dbabd31db55c209b22e38ce2f19a1956` |
| [`ag-uipol-011-b6a5bc931194-desktop-1280-strategy-performance.png`](./ag-uipol-011-b6a5bc931194-desktop-1280-strategy-performance.png) | 1280x900 | `dc0872cde4505af3a4d6ababe972a4c882a16e602d67a070921774ce41e979f2` |
| [`ag-uipol-011-b6a5bc931194-wide-2560-trading-room.png`](./ag-uipol-011-b6a5bc931194-wide-2560-trading-room.png) | 2560x1440 | `9adf87e172bf7f0d539a550a02df8dc4b282832c3946d091c264b0b416f14b8e` |
| [`ag-uipol-011-b6a5bc931194-wide-2560-strategy-workshop.png`](./ag-uipol-011-b6a5bc931194-wide-2560-strategy-workshop.png) | 2560x1440 | `59fc4d32aa16b729817b6b9f0bee6bb77ad216fed192f49eed666e55b2ed4e87` |
| [`ag-uipol-011-b6a5bc931194-wide-2560-strategy-performance.png`](./ag-uipol-011-b6a5bc931194-wide-2560-strategy-performance.png) | 2560x1440 | `e526368db59fcb9a6028f8b8382771d65a9fde3fc22b0c8fbc3e2e1afa73ae66` |

## Residual truth boundaries

- The narrow layouts collapse controls dynamically but do not change the underlying desktop grid coordinates or default workspace database structure.
- Modal/drawer transitions use focused accessibility gates; no mutation side-effects are claimed on live portfolios.
- `parity-matrix.md` rows G-06, PF-07, and SRV-03 are updated to reflect the verified narrow behavior.
