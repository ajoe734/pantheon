# AG-DYNUI-PROD-001 - Restore Agora DYNUI Source And Task Truth

Owner: Codex
Reviewer: Claude
Depends on: none
Updated: 2026-07-04

This artifact is the task-scoped source/task truth map for the Agora DYNUI
production-gap fleet. It does not implement UI behavior and does not certify the
hosted route as production-complete.

## Current Result

- The original raw design archive is still missing at the expected durable
  path: `/home/lupin/code/pantheon/AI Trading Desk Design.zip`.
- The extracted reference directory is currently readable at
  `/tmp/ai-trading-desk-design/`, but that path is volatile and is not a
  canonical repository source.
- The committed continuation source is
  `docs/04/agora_design_pack_dynui_2026-06-28/`. Until the raw zip is restored
  to the expected path, downstream workers must use the committed closure pack
  plus the readable extracted reference and must not invent design details.
- The stale nested frontend checkout `/home/lupin/code/pantheon/.fe-ep` is not
  a deploy source for this fleet. It remains a dirty historical checkout and
  must be ignored by DYNUI production-gap workers.
- Reconstructed terminal archive snapshots were added for completed DYNUI tasks
  whose PR evidence exists but whose `ai-task-archive/tasks/*.json` files were
  absent from clean `origin/dev`.

## Design Source Truth

| Source | Current status | Use in this fleet |
| --- | --- | --- |
| `/home/lupin/code/pantheon/AI Trading Desk Design.zip` | Missing on this worker and in the supervisor root checked during this task. | Exact unresolved blocker if a worker needs the raw archive. Do not claim the raw archive is restored until this file exists and lists successfully. |
| `/tmp/ai-trading-desk-design/` | Readable during this task; contains `uploads/`, `Agora.dc.html`, and screenshots including `01-v10-mid.png`, `02-v10-mid.png`, `01-applied.png`, and `01-aifix.png`. | Helpful local reference only. It is not durable task truth because `/tmp` can be cleaned. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Committed closure pack from `AG-DYNUI-SRC-001`. | Primary repository source for dynamic invariants and task graph. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | Committed frozen source/gap map. | Canonical routing source for V10/V11/V6/V4 references, screenshots, dynamic invariants, and non-static acceptance guard. |
| `docs/04/agora_design_pack_dynui_2026-06-28/closeout.md` | Committed closeout evidence. | Records PR #2538 merge `64036dbebb5d24b967cadf75e69b6983c582257d` and closeout publication evidence. |

Primary design references remain:

- `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md`
- `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md`
- `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md`
- `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md`
- `Agora.dc.html`
- `screenshots/01-v10-mid.png`, `screenshots/02-v10-mid.png`,
  `screenshots/01-applied.png`, `screenshots/01-aifix.png`

If these files cannot be read from the raw archive, extracted reference, or
committed source map, the worker must open a blocker instead of deriving new
fields, routes, widgets, or visual states from memory.

## Frontend Source And Deploy Truth

| Checkout | Observed state | Decision |
| --- | --- | --- |
| `/home/lupin/code/execute-plans` | Repository `ajoe734/execute-plans`; local branch `dev` was dirty and ahead/behind `origin/dev` during this task. Remote `origin/dev` points at merge `702b236adb76a4e9a2029fce1a4b9c487f69a290` from PR #169. Remote `origin/dev` includes PR #147 (`aa071d6...`), PR #148 (`6343755...`), and PR #168 (`ffbc235...`) for the Agora Trading Room default/auth fixes. | Canonical frontend repository for new DYNUI work, but workers must create a clean task worktree/branch from the intended remote base before editing or deploying. Do not use the dirty local checkout as a task branch. |
| `/home/lupin/code/pantheon/.fe-ep` | Dirty nested checkout on unrelated branch `task/mgmt-gap-008-detail-honesty`; it lacks the `readBffEnv` / `buildHeaders` / `authHeaders()` Trading Room auth fix visible in `execute-plans` `origin/dev`. | Historical/stale checkout only. Do not build, deploy, diff, or assign DYNUI work from this path. |
| Pantheon deploy scripts | Scoped search found `.fe-ep` only in docs/dispatch references, not in deploy scripts as the FE build root. | Stale nested checkout risk is assigned to fleet process: `AG-DYNUI-PROD-006` must prove hosted deploy source from a clean `execute-plans` commit and must not accept `.fe-ep` evidence. |

## Restored Archive Continuity

The following reconstructed archive snapshots were added because the task had
merged PR evidence but no terminal snapshot under `ai-task-archive/tasks/`:

- `AG-BE-DYNUI-001`
- `AG-BE-DYNUI-002`
- `AG-BE-DYNUI-003`
- `AG-XR-DYNUI-001`
- `AG-FE-DYNUI-001`
- `AG-FE-DYNUI-002`
- `AG-FE-DYNUI-003`
- `AG-FE-DYNUI-004`
- `AG-FE-DYNUI-005`

Each snapshot is marked `reconstructed_by: AG-DYNUI-PROD-001` and cites the PR
or closeout artifact used as evidence. `AG-E2E-DYNUI-001` was not archived
because no merged completion evidence was found; it is replaced by
`AG-DYNUI-PROD-006`.

## Completed Vs Incomplete Boundary

| Old task | Restored truth | Evidence | Boundary for production-gap fleet |
| --- | --- | --- | --- |
| `AG-DYNUI-SRC-001` | Completed; archive already existed. | Pantheon PR #2538 merge `64036db...`; closeout pack. | Raw zip is missing now, so use committed closure pack and extracted reference; do not claim raw archive restoration. |
| `AG-BE-DYNUI-001` | Completed backend workspace proposal/workspace foundation. | Pantheon PR #2577 merge `cb8b031...`; closeout PR #2579 merge `eac485c...`. | Does not prove durable DB persistence or hosted FE behavior. |
| `AG-BE-DYNUI-002` | Completed backend widget revision/version/rollback foundation. | Pantheon PR #2581 merge `b3c8e654...`. | Backend foundation only; frontend/runtime production behavior remains downstream. |
| `AG-BE-DYNUI-003` | Completed servant generator and validator integration. | Implementation PR #2585 merge `ef246b2...`; closeout PR #2587 merge `de81d70...`. | Readiness/store-backed generator caveats remain; not hosted E2E proof. |
| `AG-XR-DYNUI-001` | Completed enough to publish v1.5 Pantheon bundle evidence. | Pantheon PR #2593 merge `ab19264...`; execute-plans PR #80 merge `5e5a260...`. | execute-plans PR #80 visibly had a failed integration-gate despite being merged; treat as merged contract evidence, not production acceptance. |
| `AG-FE-DYNUI-001` | Completed V10 Strategy Workshop dynamic runtime. | Pantheon PR #2569 merge `70a8d1cf...`; Pantheon closeout PR #2575 merge `16d5d53...`. | Screenshot/Playwright evidence was deferred; not full hosted V10-to-V11 proof. |
| `AG-FE-DYNUI-002` | Completed V11 proposal preview/workspace shell slice. | execute-plans PR #81 merge `64a9631...`; Pantheon closeout PR #2602 merge `d65ae35...`. | Does not complete grid edit, revision, version history, rollback, or E2E. |
| `AG-FE-DYNUI-003` | Completed Trading Room grid editor slice. | execute-plans PR #82 merge `98516d1...`; Pantheon closeout PR #2606 merge `28ac6a9...`. | Does not complete widget revision/version/rollback E2E. |
| `AG-FE-DYNUI-004` | Completed widget adjustment drawer slice. | execute-plans PR #84 merge `ff1b3a3...`; Pantheon closeout PR #2616 merge `7d6f34f...`. | Does not by itself prove full server-backed workflow on hosted route. |
| `AG-FE-DYNUI-005` | Completed visual parity closeout on top of existing runtime. | Pantheon parent PR #2622 merge `f127bdbe...`; closeout PR #2627 merge `80c24c8...`. | Visual parity is not production completion; hosted E2E remains open. |
| `AG-E2E-DYNUI-001` | No terminal archive restored. | No merged completion PR found from scoped search. | Replaced by `AG-DYNUI-PROD-006` hosted acceptance gate. |
| `AG-DYNUI-LIVE-DEFAULT-001` / auth follow-ups | Live/cache/auth repairs exist. | execute-plans PR #147 `aa071d6...`, #148 `6343755...`, #168 `ffbc235...`; Pantheon cache/header PR #2845 `a37600e...`. | These repair access/cache/header behavior; they do not close dynamic UI production completeness. |

## Downstream Fleet Routing

- `AG-DYNUI-PROD-002`: shell architecture; decide whether Agora exits global
  `PlatformShell` or records an explicit exception.
- `AG-DYNUI-PROD-003`: default `/agora/trading-room` route must enter a dynamic
  design-pack workflow instead of an inert aggregate empty state.
- `AG-DYNUI-PROD-004`: error/stale-bundle diagnostics and retry/correlation
  behavior.
- `AG-DYNUI-PROD-005`: full proposal, grid edit, widget revision, version
  history, and rollback through strict BFF contracts.
- `AG-DYNUI-PROD-006`: hosted E2E/publish gate from a clean `execute-plans`
  deploy source, including desktop/mobile screenshots and proof that `.fe-ep`
  was not used.
