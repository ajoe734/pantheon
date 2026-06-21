# AG-FE-DB-002 Sidecar Acceptance Follow-up 16

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-FE-DB-002` - Drag/resize/add/remove/change chart editor |
| Parent owner / reviewer | `Codex` / `Claude` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | `2026-06-21` |
| Mutates canonical truth | `false` |
| Status | Ready for Codex review |

## Purpose

This packet is a support-only refresh for `AG-FE-DB-002`. It updates the
acceptance checklist, dependency map, and parent blocker handoff after
`AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-15` was reviewed, finalized, and
merged.

The parent task remains active `blocked` and `waiting_for` `Claude`. This
sidecar does not unblock, reopen, implement, or close the parent.

No runtime, registry, schema, OpenAPI, BFF, governance, broker, RuntimeBinding,
or canonical L1/L2 truth surface is changed by this packet.

## Reviewed Evidence Chain

All prior DB002 support packets through follow-up 15 are durable. Follow-up 15
is archived `done` and records Codex review approval:

| Sidecar | State | Key decision or evidence |
|---|---|---|
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE` | `done` PR #1870 | Original mirror waiver, V10/V11 waiver, 13-item acceptance checklist, dependency map |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | `done` PR #1887 | Parent blocked-status distinction and DashboardGridEditor composition gates |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | `done` PR #1894 | Current-dev compose surface and closeout refresh |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4` | `done` PR #1903 | Parent blocked-status preservation and dependency refresh |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` | `done` PR #1910 | Waiver evidence routing, dependency map refresh, support-only boundary |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-6` | `done` PR #1914 | Compressed handoff packet and parent blocked-state confirmation |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-7` | `done` PR #1917 | Evidence routing and compose-surface refresh through followup-6 |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8` | `done` PR #1922/#1923 | Evidence chain refreshed through followup-7, finalization record added |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-9` | `done` PR #1933/#1937 | Current-dev acceptance refresh through followup-8 and closeout record |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-10` | `done` PR #1942 | Current-dev acceptance refresh through followup-9 and closeout record |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-11` | `done` PR #1947 | Current-dev acceptance refresh through followup-10 and closeout record |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-12` | `done` PR #1958 | Current-dev acceptance refresh through followup-11; Codex reviewed |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | `done` PR #1963 | Current-dev acceptance refresh through followup-12; Claude2 reviewed |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | `done` PR #1972 | Current-dev acceptance refresh through followup-13; Claude2 reviewed; closeout record merged |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-15` | `done` PR #1995 | Current-dev acceptance refresh through followup-14; Codex reviewed; v1.2 contract freshness note merged |

Follow-up 15 archived review notes state that PR #1995 merged to `dev`, only
added the follow-up 15 support packet, left canonical truth and runtime
surfaces unchanged, confirmed `DashboardGridEditor` remained absent, and
confirmed current v1.2 layout route/concurrency/operation semantics.

## Current Dev Delta Since Follow-up 15

Follow-up 15 merged at `add046b8`. Current `origin/dev` during this packet is
`9a5ec4c8`.

`git log --oneline add046b8..origin/dev` shows only support-sidecar and review
material after follow-up 15:

| Area | Merged work since `add046b8` | DB002 consequence |
|---|---|---|
| FE identity support | PR #1994 / followup-25 review material and PR #1997 / followup-26 packet material landed. | No dashboard editor path or DB002 runtime change. |
| BE identity support | PR #1996 / `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` packet landed. | No dashboard editor path or DB002 runtime change. |
| DB002 support chain | Follow-up 15 is now merged and archived `done`. | This packet starts from reviewed followup-15 evidence instead of repeating unresolved followup-14 state. |

`git diff --name-only add046b8 origin/dev` lists four support artifacts only:

- `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_13.md`
- `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md`
- `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-25-REVIEW.md`
- `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26.md`

No changed file in this delta is under:

- `execute-plans/src/agora/dashboard/`
- `execute-plans/src/agora/widgets/`
- `execute-plans/src/lib/bff-v1/agora/`
- `services/control-plane/openapi/`
- `services/control-plane/specs/agora/`

## Current State Snapshot

| Surface | Observed state | Acceptance consequence |
|---|---|---|
| `AG-FE-DB-002` | Active `blocked`; owner `Codex`; reviewer `Claude`; `waiting_for` `Claude`. | Parent remains blocked until `Claude` explicitly absorbs reviewed sidecar evidence or records a new concrete blocker. |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16` | Active `in_progress` at authoring; owner `Codex2`; reviewer `Codex`; artifact is this support packet. | Owner should commit this packet, hand it to `Codex` for review, and preserve parent status unchanged. |
| `AG-XR-DASH-001` | Archived `done`. | v1.1 dashboard routes, v2 schemas, ETag/If-Match, expected version, idempotency, and 409 conflict semantics are available. |
| `AG-XR-OPENAPI-002` | Archived `done`. | v1.2 additive route/type context is merged; DB002 should refresh against it before implementation closeout. |
| `AG-BE-DB-001` | Archived `done`. | Dashboard BFF CRUD, layout PATCH, widget validator, append-only versioning, and core A3 safety rules are merged. |
| `AG-FE-DB-001` | Archived `done`. | Registry, `WidgetRenderer`, `ChartSpecRenderer`, generated Agora types, ECharts, and `react-grid-layout` dependency are merged. |
| `AG-FE-DB-003` | Archived `done`. | `WidgetRevisionDrawer` and before/after widget change flow are merged. DB002 must compose this surface instead of forking widget revision UX. |
| `AG-FE-DB-004` | Archived `done`. | `DashboardProposalPreview`, `DashboardChangeLog`, rollback/proposal UI, and dashboard tests are merged. DB002 must not duplicate those ownership areas. |
| `DashboardGridEditor` | `execute-plans/src/agora/dashboard/DashboardGridEditor.tsx` is absent on current dev. | Parent implementation remains incomplete; this support packet makes no runtime delivery claim. |
| Layout PATCH helper | `execute-plans/src/lib/bff-v1/agora/dashboard.ts` remains 113 lines and exposes only the widget validation helper. | DB002 still likely needs a narrow typed layout PATCH helper, but UI components must avoid direct `fetch()`. |
| Typed route inventory | `execute-plans/src/lib/bff-v1/agora/types.ts` includes `patchDashboardRecipeLayout` for `/bff/agora/dashboard-recipes/{recipe_id}/layout`. | Parent owner should use generated names and current request semantics instead of inventing route bodies. |
| `AG-FE-TR-002` | Active `todo`; owner `Claude`; reviewer `Codex`. | Trading Room queue UI is separate and does not unblock DB002. |
| `AG-E2E-TR-001` | Active `todo`; depends on `AG-FE-TR-002` and `AG-FE-DB-002`. | E2E must wait for DB002 implementation, review, merge, and closure. |

## Parent Blocker Absorption

The parent blocker still cites repository/dependency routing conflict and
missing V10/V11 visual references. The reviewed support chain through
follow-up 15 already answers both points. Follow-up 16 adds only the
current-dev delta confirmation that no newer dashboard/editor/runtime surface
landed after follow-up 15.

| Blocker point | Reviewed answer to absorb |
|---|---|
| `execute-plans/` is gitignored as a phantom mirror | Intentional `execute-plans/` task files may be committed only through `scripts/git/worker_commit.py` with explicit file paths in `--scope`. Raw `git add .`, raw `git add -A`, and directory-scope `--scope execute-plans/` remain forbidden. |
| Clean sibling `execute-plans` lacks AG-FE-DB-001 artifacts | The reviewed support chain treats the Pantheon `execute-plans/` mirror artifacts merged by AG-FE-DB-001/003/004 as the current compose surface for this Agora wave unless the parent reviewer/supervisor gives a new routing decision. |
| Missing V10/V11 visual snapshots | Missing snapshots do not block functional DB002 work. Binding authority is the contract-closure prose, v2/v1.2 schemas, A3 widget registry/chart grammar, and existing `execute-plans/src/agora/` component/token conventions. |
| New v1.2 Agora contract bundle landed after the original DB002 blocker | This is not a new DB002 blocker by itself, but parent implementation should refresh route/type assumptions against `AG-XR-OPENAPI-002` evidence before writing or closing DB002. If generated front-end types are stale, stop and record a parent blocker instead of hand-writing contract shapes. |
| Dev advanced after follow-up 15 | The new delta is limited to other sidecar/review artifacts. It does not add `DashboardGridEditor`, change the dashboard BFF helper, or alter Agora dashboard contract files. |

Recommended parent path:

1. `Claude`, as the parent reviewer and current `waiting_for`, explicitly
   acknowledges reviewed sidecar evidence through follow-up 16 or records a new
   concrete blocker.
2. If acknowledged, `Claude` reopens the parent for owner implementation:

   ```bash
   AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-DB-002 "Reviewed DB002 sidecar waiver and current-dev evidence accepted through followup-16; return parent to Codex for DashboardGridEditor implementation."
   ```

3. `Codex`, as parent owner, implements only the narrow DB002 runtime slice:
   `DashboardGridEditor`, focused tests, and a typed layout PATCH helper only
   if the existing Agora BFF helper surface still lacks one.
4. Any ambiguity in route shape, field spelling, UI authority, mirror routing,
   generated type freshness, or dependency ownership should become a parent
   blocker. The parent brief still prohibits filling gaps by inference.

## Current Dev Compose Surface

| Surface | Current file or dependency | DB002 usage rule |
|---|---|---|
| Registry gate | `execute-plans/src/agora/widgets/registry.ts` | Use `validateWidgetSpecAgainstRegistry`, active registry metadata, and sensitivity checks. Do not create a second allowlist. |
| Widget rendering | `execute-plans/src/agora/widgets/WidgetRenderer.tsx` | Every grid frame renders through `WidgetRenderer`; pass the user's allowed sensitivity scope. |
| Chart rendering | `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` | Delegate chart display; do not introduce arbitrary HTML/JS, iframe, `eval`, `new Function`, or `dangerouslySetInnerHTML`. |
| Widget revision | `execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx` | Chart/widget change UX should compose this drawer or its accepted `WidgetSpecV2` result boundary. |
| Proposal/history/rollback | `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx`, `DashboardChangeLog.tsx` | Preserve DB004 ownership of proposal, version, rollback, and change-log behavior. |
| Typed Agora contracts | `execute-plans/src/lib/bff-v1/agora/types.ts` | Use generated names and current route metadata; do not invent route bodies or enum values. |
| Dashboard BFF helper | `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Keep BFF fetch details in helper code; add only a narrow layout PATCH helper if DB002 needs one. UI components should not make raw route calls. |
| v1.2 layout contract | `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Confirms `PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout`, `If-Match`, `Idempotency-Key`, `expected_version`, and the six layout operations. |
| Layout dependency | `react-grid-layout` `^1.5.0`, `@types/react-grid-layout` `^1.3.5` | Use this library for drag/resize; no alternate grid library or custom drag engine. |
| Chart dependency | `echarts` `^5.6.0`, `echarts-for-react` `^3.0.2`, `recharts` `^2.15.4` | Continue the existing chart stack. No dependency-only change is needed for DB002. |

## Parent Acceptance Checklist

| Area | Parent pass condition |
|---|---|
| File scope | Any commit touching `execute-plans/` uses explicit file paths with `worker_commit.py --scope`; no raw staging sweep and no directory-scope mirror commit. |
| Component ownership | Add `DashboardGridEditor` and focused tests only unless a typed layout PATCH helper is strictly required. |
| Contract freshness | Before implementation closeout, confirm generated Agora types and helper calls align with current v1.2 dashboard route semantics. Do not hand-write missing contract shapes. |
| Grid library | Use `react-grid-layout`; no alternate grid library and no custom drag engine. |
| Editable gestures | Tests cover drag, resize, add, remove, and chart-change. |
| Placement shape | Layout mutations produce `WidgetPlacement`-compatible records with `widget_id`, `x`, `y`, `w`, `h`, `min_w`, `min_h`, and preserve optional `max_w`, `max_h`, `pinned`. |
| Patch operation allowlist | Layout writes use only `move_widget`, `resize_widget`, `remove_widget`, `add_registered_widget`, `replace_chart_spec`, or `update_widget_query`. |
| BFF route | Layout PATCH targets `/bff/agora/dashboard-recipes/{recipe_id}/layout` through the typed Agora BFF helper surface. |
| Concurrency | State-changing layout writes include current ETag/`If-Match`, `expected_version`, and `Idempotency-Key`; 409 `CONCURRENT_MODIFICATION` is visible and never overwritten silently. |
| Personalization event | Every layout or chart mutation emits a schema-compatible `PersonalizationEvent` with dashboard recipe context. |
| Registry validation | Add/change flows call the merged registry gate and, where server validation is needed, the BFF widget validate helper. Unknown, inactive, unsupported chart kind, blocked interaction, unapproved data source, or sensitivity downgrade cases fail closed. |
| Renderer composition | Every widget frame renders through `WidgetRenderer`; DB002 must not fork chart rendering or built-in widget cards. |
| Sensitivity | Pass allowed sensitivity context to `WidgetRenderer`; do not render data above operator scope. |
| Pinned guard | `pinned: true` placements cannot be moved or resized; tests cover this guard. |
| DB003 composition | Assistant-driven chart/widget changes compose `WidgetRevisionDrawer` or its accepted result instead of a parallel conversation flow. |
| DB004 composition | Recipe proposal, change-log, version, and rollback behavior remains owned by DB004 surfaces and backend contract. |
| Runtime boundary | No order placement, broker invocation, capital binding, RuntimeBinding write, management route, arbitrary HTML/JS, iframe, `eval`, `new Function`, or `dangerouslySetInnerHTML`. |
| Verification | Focused editor tests, widget/dashboard regression tests, `build:agora`, contract drift checks when generated contract surfaces are touched, and `git diff --check` are recorded in parent closeout. |

## Dependency Map

```mermaid
graph TD
    XRDASH["AG-XR-DASH-001 done<br/>v1.1 routes + v2 schemas + concurrency"] --> OPENAPI002["AG-XR-OPENAPI-002 done<br/>additive Agora v1.2 bundle"]
    XRDASH --> BEDB["AG-BE-DB-001 done<br/>BFF CRUD + layout PATCH + validator"]
    XRDASH --> DB001["AG-FE-DB-001 done<br/>registry + WidgetRenderer + ChartSpecRenderer + deps"]
    OPENAPI002 --> DB002["AG-FE-DB-002 active blocked<br/>DashboardGridEditor absent"]
    BEDB --> DB002
    DB001 --> DB002
    DB003["AG-FE-DB-003 done<br/>WidgetRevisionDrawer"] --> DB002
    DB004["AG-FE-DB-004 done<br/>ProposalPreview + ChangeLog + rollback"] --> DB002
    P0["Original sidecar done PR #1870<br/>mirror + V10/V11 waivers"] -. reviewed evidence .-> DB002
    P14["FOLLOWUP-14 done PR #1972<br/>review + closeout merged"] -. reviewed evidence .-> DB002
    P15["FOLLOWUP-15 done PR #1995<br/>v1.2 freshness note reviewed"] -. reviewed evidence .-> DB002
    P16["FOLLOWUP-16 this packet<br/>post-followup-15 dev delta"] -. review handoff .-> DB002
    DB002 --> E2E["AG-E2E-TR-001 todo<br/>depends on AG-FE-TR-002 + DB002"]
```

Dependency notes:

- Upstream DB002 implementation dependencies remain merged and archived `done`.
- `AG-XR-OPENAPI-002` adds current contract context, not a dashboard editor
  implementation.
- The only dev delta after follow-up 15 is unrelated sidecar/review material.
- `DashboardGridEditor` remains absent, so DB002 cannot be represented as
  runtime-complete.
- `AG-E2E-TR-001` must wait for DB002 parent implementation and closure.

## Suggested Parent Verification

Once the parent implementation exists:

```bash
npm --prefix execute-plans test -- --run src/agora/dashboard/DashboardGridEditor
npm --prefix execute-plans test -- --run src/agora/widgets src/agora/dashboard
npm --prefix execute-plans run build:agora
git diff --check
```

Keep these contract checks if DB002 touches generated Agora contract surfaces:

```bash
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_v1_2_bundle.py -q
```

If broad TypeScript or lint commands remain blocked by unrelated baseline
failures, the parent owner should record the exact focused passing commands and
the unrelated failure signature.

## Sidecar Verification Performed

Commands used while preparing this support packet:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git merge --ff-only origin/dev
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-15
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DASH-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-TR-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-E2E-TR-001
git log --oneline --decorate --graph --max-count=24 origin/dev
git log --oneline add046b8..origin/dev
git diff --name-only add046b8 origin/dev
git diff --stat add046b8 origin/dev
find execute-plans/src/agora/dashboard -maxdepth 1 -name DashboardGridEditor.tsx -print
grep -E "react-grid-layout|@types/react-grid-layout|echarts|echarts-for-react|recharts" execute-plans/package.json
wc -l execute-plans/src/lib/bff-v1/agora/dashboard.ts
rg -n "dashboard-recipes|patchDashboardRecipeLayout|WidgetPlacement|PersonalizationEvent|CONCURRENT_MODIFICATION|Idempotency-Key|If-Match|expected_version|move_widget|resize_widget|remove_widget|add_registered_widget|replace_chart_spec|update_widget_query" services/control-plane/openapi/agora_v1_2.openapi.yaml services/control-plane/specs/agora/v3 execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/agora/dashboard.ts
rg -n "DashboardGridEditor|WidgetRenderer|ChartSpecRenderer|WidgetRevisionDrawer|DashboardProposalPreview|DashboardChangeLog|validateWidgetSpecAgainstRegistry" execute-plans/src/agora execute-plans/src/lib/bff-v1/agora
```

Observed results:

- Branch is `task/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-16` at
  `origin/dev` checkpoint `9a5ec4c8`.
- Working tree had one untracked entry before authoring:
  `.orchestrator/task-briefs/ag_fe_db_002_sidecar_acceptance_followup_16.md`
  (task-scoped brief - expected).
- This sidecar is active `in_progress`, owned by `Codex2`, reviewed by `Codex`.
- Parent `AG-FE-DB-002` remains active `blocked`, waiting for `Claude`.
- Follow-up 15 is archived `done` with Codex review approval and PR #1995
  merged at `add046b8`.
- Since `add046b8`, dev advanced only through other sidecar/review artifacts.
  No dashboard editor, dashboard widget, Agora BFF helper, OpenAPI, schema,
  runtime, registry, governance, broker, or RuntimeBinding implementation file
  changed in that delta.
- `DashboardGridEditor.tsx` is absent on current dev.
- `react-grid-layout`, `@types/react-grid-layout`, `echarts`,
  `echarts-for-react`, and `recharts` are present in
  `execute-plans/package.json`.
- `execute-plans/src/lib/bff-v1/agora/dashboard.ts` remains 113 lines and
  contains only the widget validation BFF helper.
- v1.2 OpenAPI confirms the dashboard layout PATCH path, headers, expected
  version, conflict semantics, and six layout operation values.
- No canonical truth, schema, OpenAPI, runtime, registry, governance, broker,
  or RuntimeBinding implementation was changed by this sidecar.

## Reviewer Handoff

To `Codex`, sidecar reviewer:

Please review this packet for:

1. Accuracy of the post-followup-15 dev delta summary.
2. Completeness of the parent acceptance checklist for `DashboardGridEditor`.
3. Correctness of the support-only boundary: no canonical truth, runtime,
   schema, registry, governance, broker, or RuntimeBinding surface is changed.
4. Whether this packet is sufficient for parent reviewer `Claude` to either
   absorb the reviewed evidence or record a new concrete parent blocker.
