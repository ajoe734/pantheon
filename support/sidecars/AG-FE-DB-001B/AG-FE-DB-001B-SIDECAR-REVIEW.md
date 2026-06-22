# AG-FE-DB-001B Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-001B-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-001B` - Deliver Agora dashboard widget runtime to execute-plans |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | `2026-06-22` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

## Purpose

This packet supports `AG-FE-DB-001B` by consolidating review evidence for the
frontend widget runtime delivery record and by making the reviewer handoff
explicit.

It is support-only. It does not change L1 canonical truth, schema truth,
OpenAPI truth, BFF runtime behavior, frontend runtime behavior, widget
registry behavior, governance implementation, broker authority, or
RuntimeBinding.

## Sources Used

| Source | Relevance |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B-SIDECAR-REVIEW` | Sidecar owner, reviewer, support-only acceptance, and artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B` | Parent status, owner/reviewer, acceptance scope, and review notes. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001` | Archived implementation commit, original widget renderer scope, and review caveats. |
| GitHub PR `#2175` | Parent evidence PR state, merge commit, head commit, and required check results. |
| GitHub PR `#2178` | Parent closeout PR state, merge commit, head commit, and required check results. |
| Commit `82a02babed77b5ece209e0a217328aa08b29ce79` | Parent `AG-FE-DB-001B` acceptance evidence record. |
| Commit `59281b585c113c913ffad2bd1f179c368dfe531c` | Parent task-brief closeout update marking `AG-FE-DB-001B` done. |
| Commit `6062cb2cc850f032de9b890a47db55a60a6033cf` | Original widget registry/renderer implementation commit referenced by the parent evidence record. |
| `origin/dev:.orchestrator/task-briefs/ag_fe_db_001b.md` | Latest parent brief status, closeout section, and composition notes. |
| `execute-plans/src/agora/widgets/registry.ts` | Registry constants, checksum pins, active-widget gate, ChartSpec allowlists, blocked interactions, and sensitivity validation. |
| `execute-plans/src/agora/widgets/WidgetRenderer.tsx` | Registry-gated shell and props-fed render path. |
| `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` | Recharts/ECharts/builtin chart dispatch and unsafe render marker guard. |
| `execute-plans/src/agora/widgets/*.test.*` | Focused verification for registry, renderer, chart dispatch, unsafe spec rejection, and revision drawer composition. |
| `execute-plans/package.json` | Approved dependency surface for ECharts, Recharts, react-grid-layout, and Vitest. |

## Parent Delivery Facts

| Item | Evidence |
|---|---|
| Parent L0 status observed through `ai-status` | Active `review_approved`; owner `Claude`, reviewer `Claude2`. |
| Parent review notes | `17/17` tests independently verified; all acceptance criteria met. |
| Parent evidence PR | `https://github.com/ajoe734/pantheon/pull/2175` |
| Parent evidence PR state | `MERGED` into `dev` at `2026-06-22T01:44:21Z`. |
| Parent evidence merge commit | `7b18c6c149f6065cba6543624e4dcefd84694cf6` |
| Parent evidence commit | `82a02babed77b5ece209e0a217328aa08b29ce79` |
| Parent closeout PR | `https://github.com/ajoe734/pantheon/pull/2178` |
| Parent closeout PR state | `MERGED` into `dev` at `2026-06-22T02:10:11Z`. |
| Parent closeout merge commit | `64bd78cbcbfe6e7c2ebce3573754ec552fcfc125` |
| Parent closeout commit | `59281b585c113c913ffad2bd1f179c368dfe531c` |
| Parent latest task brief | `origin/dev:.orchestrator/task-briefs/ag_fe_db_001b.md` marks status `done` and records the 2026-06-22 closeout. |
| Parent PR checks | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator reported `SUCCESS` across PRs `#2175` and `#2178`. |
| Original implementation commit | `6062cb2cc850f032de9b890a47db55a60a6033cf` (`AG-FE-DB-001: add Agora widget renderers`). |
| Files under parent scope | `execute-plans/src/agora/widgets/registry.ts`, `WidgetRenderer.tsx`, `ChartSpecRenderer.tsx`, `execute-plans/package.json`. |

## Review Matrix

| Area | Evidence | Sidecar assessment |
|---|---|---|
| Support-only boundary | This packet adds only `support/sidecars/AG-FE-DB-001B/AG-FE-DB-001B-SIDECAR-REVIEW.md`. | No canonical truth or runtime implementation is changed by this sidecar. |
| Parent delivery state | `ai-status` reports parent `AG-FE-DB-001B` as active `review_approved`, while merged closeout PR `#2178` updates the task brief to `done`. | This sidecar records the state mismatch but does not mutate parent status or closeout truth. |
| Registry checksum and catalog | `registry.ts` pins `AGORA_WIDGET_CONTRACT_HASHES`; `registry.test.ts` hashes `widget_registry.v1.json` and checks 42 registry entries. | Frontend registry evidence remains tied to the frozen A3 catalog and generated contract snapshot. |
| Active widget gate | `validateWidgetSpecAgainstRegistry` rejects unknown or inactive widget types and unapproved data sources. | Renderer entry is registry-gated before display. |
| ChartSpec grammar | `CHART_SPEC_KINDS`, encoding channels, transforms, and interactions are allowlisted in `registry.ts`; tests cover allowlist lengths and deviations. | Declarative ChartSpec dispatch is bounded by local allowlists. |
| Renderer dispatch | `chartRendererForKind` maps metric/line/area/bar to Recharts, table/timeline/stacked_bar to builtin, and complex charts to ECharts. | Dispatch matches the parent acceptance split. |
| Unsafe rendering | `ChartSpecRenderer.tsx` rejects callback, HTML, script, JavaScript URL, `eval`, and function markers; tests cover unsafe options and `place_order`. | Agent output remains declarative and cannot introduce executable chart callbacks through this surface. |
| Data path | `WidgetRenderer` accepts `data` as props; static search over `execute-plans/src/agora/widgets` found no direct `fetch`, `axios`, or `XMLHttpRequest`. | Widget components do not fetch directly from the page tree. |
| Runtime/broker boundary | Registry blocks `place_order`, `submit_order`, `enable_live`, `bind_capital`, `runtime_binding`, and `invoke_broker`; static search found those only as blocked constants or test fixtures. | No live trading, RuntimeBinding, or broker invocation path is introduced in the widget runtime. |
| Dependency surface | `package.json` includes `echarts`, `echarts-for-react`, `react-grid-layout`, `@types/react-grid-layout`, `recharts`, and `vitest`. | Dependency additions expected by the parent task are present. |

## Verification Run

| Command | Result |
|---|---|
| `npm --prefix execute-plans test -- src/agora/widgets` before dependency install | Failed because local `vitest` was absent (`sh: 1: vitest: not found`). |
| `npm --prefix execute-plans run build:agora` before dependency install | Failed because local `vite` was absent (`sh: 1: vite: not found`). |
| `npm --prefix execute-plans ci` | Passed; installed 402 packages from the existing lockfile. Reported 4 audit findings and Recharts deprecation warnings; no tracked package diff. |
| `npm --prefix execute-plans test -- src/agora/widgets` | Passed: 4 files, 17 tests. |
| `npm --prefix execute-plans run build:agora` | Passed: Vite built `dist/agora/agora.html` and one app bundle. |
| `rg -n "fetch\(|axios|XMLHttpRequest|innerHTML|dangerouslySetInnerHTML|eval\(|new Function|javascript:" execute-plans/src/agora/widgets` | Only the explicit unsafe-marker guard patterns appeared; no direct fetch or DOM injection call in widget code. |
| `git status --short -- execute-plans/package.json execute-plans/package-lock.json execute-plans/src/agora/widgets` | Clean after `npm ci`; no tracked runtime source or package diff. |

## Residual Caveats

| Caveat | Recommended handling |
|---|---|
| Parent `AG-FE-DB-001B` has merged task-brief closeout evidence, but local `ai-status` still reports active `review_approved`. | Parent owner/supervisor should reconcile L0 status separately if archival `done` is still required; this sidecar must not repair parent state. |
| `npm ci` reported 4 existing audit findings and a Recharts 2.x deprecation warning. | Out of scope for this support slice; do not run `npm audit fix` here because it would change dependency truth. |
| This sidecar validates the support packet and parent evidence record; it is not a second canonical implementation review of every widget line. | Reviewer should approve or request packet edits only within the support-only scope. |

## Reviewer Handoff

Please review this packet for:

1. Accurate parent facts for PR `#2175`, evidence merge commit
   `7b18c6c149f6065cba6543624e4dcefd84694cf6`, evidence commit
   `82a02babed77b5ece209e0a217328aa08b29ce79`, closeout PR `#2178`,
   closeout merge commit `64bd78cbcbfe6e7c2ebce3573754ec552fcfc125`, and
   closeout commit `59281b585c113c913ffad2bd1f179c368dfe531c`.
2. Correct support-only boundary: no canonical truth, schema, OpenAPI, runtime,
   registry implementation, BFF behavior, broker authority, or RuntimeBinding
   mutation by this sidecar.
3. Sufficiency of the verification record for the sidecar task acceptance:
   support artifact created, no canonical truth edits, and handoff to assigned
   reviewer.

Prepared by `Codex` for the `AG-FE-DB-001B-SIDECAR-REVIEW` support slice.
