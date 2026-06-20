# AG-FE-DB-002 Sidecar Acceptance Follow-up 8

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-FE-DB-002` - Drag/resize/add/remove/change chart editor |
| Parent owner / reviewer | `Codex` / `Claude` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | `2026-06-20` |
| Mutates canonical truth | `false` |
| Status | Ready for review |

## Purpose

This packet is a support-only follow-up for `AG-FE-DB-002`. It refreshes the
handoff evidence after `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-7` merged via
PR #1917. The parent task remains active `blocked` and `waiting_for` `Claude`.
No new blocker has been raised; the existing waiver decisions and compose-surface
map remain in force.

This packet does not change parent status, implement the editor, edit canonical
truth, or change any runtime, registry, schema, BFF, governance, broker, or
RuntimeBinding surface.

## Reviewed Evidence Chain

All prior sidecar evidence is archived and durable:

| Sidecar | Archive state | Key decisions reviewed |
|---|---|---|
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE` | `done` PR #1870 | Mirror waiver, V10/V11 waiver, 13-item checklist, dependency map |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | `done` PR #1887 | Blocked-status distinction, composition gates |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | `done` PR #1894 | Current-dev compose surface, closeout refresh |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4` | `done` PR #1903 | Parent blocked-status preservation, dependency refresh |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5` | `done` PR #1910 | Waiver evidence routing, current-dev dependency map refresh, support-only boundaries |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-6` | `done` PR #1914 | Handoff packet compression, dependency map refresh, parent blocked-state confirmed |
| `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-7` | `done` PR #1917 | Evidence routing refresh, compose surface refresh, parent blocked-state confirmed |

## Current State Snapshot

| Surface | Observed state | Acceptance consequence |
|---|---|---|
| `AG-FE-DB-002` | Active `blocked`; owner `Codex`; reviewer `Claude`; `waiting_for` `Claude`. | The parent remains blocked until `Claude` explicitly absorbs the reviewed sidecar evidence and returns the parent to `Codex`. |
| `AG-XR-DASH-001` | Archived `done`. | v1.1 route semantics and v2 schema field spelling are in place. DB002 must use these. |
| `AG-BE-DB-001` | Archived `done`. | Dashboard BFF CRUD, WidgetSpec validator, ETag/If-Match concurrency, and critical A3 safety rules are merged. |
| `AG-FE-DB-001` | Archived `done`. | Registry, `WidgetRenderer`, `ChartSpecRenderer`, generated types, ECharts, and `react-grid-layout` dependency are merged. |
| `AG-FE-DB-003` | Archived `done`. | `WidgetRevisionDrawer` and backend widget validate integration are merged. |
| `AG-FE-DB-004` | Archived `done`. | `DashboardProposalPreview`, `DashboardChangeLog`, rollback/proposal UI, and tests are merged. |
| `DashboardGridEditor` | `execute-plans/src/agora/dashboard/DashboardGridEditor.tsx` is absent on current dev. | Parent implementation remains incomplete; this packet makes no runtime delivery claim. |
| `AG-FE-TR-002` | Active `todo`; owner `Claude`; reviewer `Codex`. | Trading Room queue UI work is separate from DB002. |
| `AG-E2E-TR-001` | Active `todo`; depends on `AG-FE-TR-002` and `AG-FE-DB-002`. | E2E must not start or claim dashboard closure until DB002 is implemented, reviewed, merged, and closed. |

## Parent Blocker Absorption Decision

The reviewed sidecar answers remain unchanged:

| Blocker | Reviewed answer to absorb |
|---|---|
| `execute-plans/` mirror is gitignored | Intentional `execute-plans/` files may be committed only through `scripts/git/worker_commit.py` with explicit file paths in `--scope`. Directory-scope `--scope execute-plans/`, raw `git add .`, and raw `git add -A` remain forbidden. |
| Missing V10/V11 visual snapshots | Missing snapshots do not block functional DB002 work. Binding authority is the contract-closure prose, v2 schemas, A3 widget registry/chart grammar, and existing `execute-plans/src/agora/` component/token conventions. |

Recommended parent path:

1. `Claude`, as parent reviewer and current `waiting_for`, acknowledges the
   reviewed sidecar evidence (all prior packets through followup-7 are archived
   and reviewed) or states any remaining unresolved question.
2. If acknowledged, `Claude` returns the parent to `Codex` via:

   ```bash
   AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-DB-002 "Reviewed DB002 sidecar waiver evidence accepted through followup-8; return parent to Codex for DashboardGridEditor implementation."
   ```

3. `Codex`, as parent owner, resumes only the narrow implementation slice:
   `DashboardGridEditor`, focused tests, and a typed BFF layout helper only if
   required for the already-merged layout PATCH contract.
4. Any new ambiguity in route shape, schema field spelling, UI authority, or
   dependency routing becomes a parent blocker. The parent brief prohibits
   filling gaps by inference.

## Current Dev Compose Surface

| Surface | Current file or dependency | DB002 usage rule |
|---|---|---|
| Registry gate | `execute-plans/src/agora/widgets/registry.ts` | Use `validateWidgetSpecAgainstRegistry`, active widget types, sensitivity rules, and registry constants. Do not create a second allowlist. |
| Widget rendering | `execute-plans/src/agora/widgets/WidgetRenderer.tsx` | Every grid frame renders through `WidgetRenderer`. |
| Chart rendering | `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` | Delegate chart display. Do not use arbitrary HTML/JS, iframe, `eval`, `new Function`, or `dangerouslySetInnerHTML`. |
| Widget revision | `execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx` | Assistant-driven chart/widget changes should compose this drawer or consume its validated result boundary. |
| Proposal/history/rollback | `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx`, `DashboardChangeLog.tsx` | Reuse existing proposal, version, rollback, and change-log semantics. |
| Typed Agora contracts | `execute-plans/src/lib/bff-v1/agora/types.ts` | Use generated names and v2 schema field spelling. |
| Widget validate helper | `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Keep BFF fetch details inside helper code; UI components should not invent raw route calls. |
| Layout dependency | `react-grid-layout` `^1.5.0`; `@types/react-grid-layout` `^1.3.5` | Use this library for drag/resize. No alternate grid library or custom drag engine. |
| Chart dependency | `echarts` `^5.6.0`; `echarts-for-react` `^3.0.2` | Use existing chart stack where DB002 needs chart composition. No dependency-only change is needed. |

## Parent Acceptance Checklist

| Area | Parent pass condition |
|---|---|
| File scope | Any commit touching `execute-plans/` passes explicit file paths to `worker_commit.py --scope`; no raw sweep and no directory-scope mirror commit. |
| Component ownership | Add `DashboardGridEditor` and focused tests only unless a typed BFF layout helper is strictly required. |
| Grid library | Use `react-grid-layout`; no alternate grid library or custom drag engine. |
| Editable gestures | Tests cover drag, resize, add, remove, and chart-change. |
| Placement shape | Mutations produce `WidgetPlacement`-compatible records with `widget_id`, `x`, `y`, `w`, `h`, `min_w`, `min_h`, and preserve optional `max_w`, `max_h`, `pinned`. |
| Patch op allowlist | Layout writes use only `move_widget`, `resize_widget`, `remove_widget`, `add_registered_widget`, `replace_chart_spec`, or `update_widget_query`. |
| BFF route | Layout PATCH targets `/bff/agora/dashboard-recipes/{recipe_id}/layout` through the typed Agora BFF helper surface. |
| Concurrency | State-changing layout writes include current ETag/`If-Match`, `expected_version`, and `Idempotency-Key`; 409 `CONCURRENT_MODIFICATION` is visible and never overwritten silently. |
| Personalization event | Every layout/chart mutation emits a schema-compatible `PersonalizationEvent` with dashboard recipe context. |
| Registry validation | Add/change flows call the merged registry gate and, where server validation is needed, the BFF widget validate helper. Unknown, inactive, unsupported chart kind, blocked interaction, unapproved data source, or sensitivity downgrade cases fail closed. |
| Renderer composition | Every widget frame renders through `WidgetRenderer`; DB002 must not fork chart rendering or builtin widget cards. |
| Sensitivity | Pass allowed sensitivity context to `WidgetRenderer`; do not render data above operator scope. |
| Pinned guard | `pinned: true` placements cannot be moved or resized; tests cover this guard. |
| DB003 composition | Assistant-driven chart/widget changes compose `WidgetRevisionDrawer` or its accepted `WidgetSpecV2` result instead of a parallel conversation flow. |
| DB004 composition | Recipe proposal, change-log, version, and rollback behavior remains owned by DB004 surfaces and backend contract. |
| Runtime boundary | No order placement, broker invocation, capital binding, RuntimeBinding write, management route, arbitrary HTML/JS, iframe, `eval`, `new Function`, or `dangerouslySetInnerHTML`. |
| Verification | Focused dashboard/editor tests, widget/dashboard regression tests, `build:agora`, contract drift checks when generated contract surfaces are touched, and `git diff --check` are recorded in the parent closeout. |

## Dependency Map

```mermaid
graph TD
    XRDASH["AG-XR-DASH-001 done<br/>v1.1 routes + v2 schemas + concurrency"] --> BEDB["AG-BE-DB-001 done<br/>BFF CRUD + validator"]
    XRDASH --> DB001["AG-FE-DB-001 done<br/>registry + WidgetRenderer + ChartSpecRenderer + deps"]
    BEDB --> DB002["AG-FE-DB-002 active blocked<br/>DashboardGridEditor missing"]
    DB001 --> DB002
    DB003["AG-FE-DB-003 done<br/>WidgetRevisionDrawer"] --> DB002
    DB004["AG-FE-DB-004 done<br/>ProposalPreview + ChangeLog + rollback"] --> DB002
    P0["Original sidecar done PR #1870<br/>mirror + V10/V11 waivers"] -. reviewed evidence .-> DB002
    P2["FOLLOWUP-2 done PR #1887<br/>blocked-status distinction"] -. reviewed evidence .-> DB002
    P3["FOLLOWUP-3 done PR #1894<br/>current-dev compose surface"] -. reviewed evidence .-> DB002
    P4["FOLLOWUP-4 done PR #1903<br/>closeout refresh"] -. reviewed evidence .-> DB002
    P5["FOLLOWUP-5 done PR #1910<br/>dependency map refresh"] -. reviewed evidence .-> DB002
    P6["FOLLOWUP-6 done PR #1914<br/>handoff packet compression"] -. reviewed evidence .-> DB002
    P7["FOLLOWUP-7 done PR #1917<br/>evidence routing refresh"] -. reviewed evidence .-> DB002
    P8["FOLLOWUP-8 this packet<br/>evidence chain refresh"] -. reviewed evidence .-> DB002
    DB002 --> E2E["AG-E2E-TR-001 todo<br/>depends on AG-FE-TR-002 + DB002"]
```

Dependency notes:

- Upstream DB002 implementation dependencies are merged and archived `done`.
- The remaining parent issue is coordination/status absorption of reviewed
  blocker evidence, not a missing schema, route, registry, or library
  dependency.
- `DashboardGridEditor` remains the missing runtime slice.
- This sidecar does not modify parent status and does not replace parent
  reviewer approval.

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
```

If full TypeScript or lint remains blocked by unrelated baseline failures, the
parent owner should record the exact focused passing commands and the unrelated
failure signature.

## Sidecar Verification Performed

Commands used while preparing this support packet:

```bash
git status --short
git branch --show-current
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-DB-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-DB-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-DB-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-DB-004
AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-DASH-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-DB-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-E2E-TR-001
git log --oneline -10 origin/dev
```

Observed results:

- Branch is `task/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8` checked out
  from current `origin/dev`.
- Supervisor status root reports this sidecar active `in_progress`, owned by
  `Claude`, reviewed by `Claude2`.
- The local worktree had only the generated task-scoped brief (untracked)
  before this packet was authored.
- Parent `AG-FE-DB-002` remains active `blocked`, waiting for `Claude`.
- Original sidecar and follow-ups 2, 3, 4, 5, 6, and 7 are archived `done`
  (PR #1870, #1887, #1894, #1903, #1910, #1914, #1917 respectively).
- `AG-FE-DB-001`, `AG-BE-DB-001`, `AG-XR-DASH-001`, `AG-FE-DB-003`, and
  `AG-FE-DB-004` are archived `done`.
- `AG-FE-TR-002` and `AG-E2E-TR-001` are active `todo`; `AG-E2E-TR-001`
  depends on `AG-FE-DB-002`.
- `DashboardGridEditor` is absent from current dev.
- `react-grid-layout`, `@types/react-grid-layout`, ECharts, and
  `echarts-for-react` are present in `execute-plans/package.json`.
- No canonical truth, schema, OpenAPI, runtime, registry, governance, broker,
  or RuntimeBinding implementation was changed by this sidecar.

## Reviewer Handoff

`Claude2` should review this support packet for:

1. Whether it accurately preserves parent `AG-FE-DB-002` as active `blocked`
   while routing reviewed waiver evidence (through followup-7) back to the
   parent reviewer/owner.
2. Whether the current-dev compose surface and dependency map are accurate.
3. Whether the acceptance checklist is complete enough for the missing
   `DashboardGridEditor` parent implementation.
4. Whether the support-only boundary is preserved.

Suggested reviewer command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8.md ./scripts/ai-status.sh approve AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8 "Review approved: DB002 follow-up 8 preserves parent blocked status, routes reviewed waiver evidence from prior sidecars including followup-7 PR #1917, refreshes current-dev dependency map, and keeps support-only boundaries without canonical/runtime changes."
```

Prepared by `Claude` for the `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-8`
support slice.
