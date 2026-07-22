# Agora Design Pack Dynamic UI Source Map And Gap Map

Date: 2026-06-28
Task: AG-DYNUI-SRC-001
Owner: Codex
Reviewer: Codex2

This file freezes the source map for the `AI Trading Desk Design.zip` dynamic
UI work. It is intentionally an intake artifact: it maps the design sources to
current implementation surfaces and blocks static-page substitutions. It does
not add missing schemas, routes, widgets, or UI behavior.

## Blocker Check

- Design archive was readable at
  `/home/lupin/code/pantheon/AI Trading Desk Design.zip`.
- Extracted reference was readable at `/tmp/ai-trading-desk-design/`.
- No STOP blocker was found from unreadable source material.
- Current Pantheon/execute-plans implementation is materially incomplete for
  V10/V11, but the incompleteness is a known implementation gap rather than a
  source/schema contradiction. Downstream tasks must implement the gaps through
  explicit schemas, routes, generated types, validators, and UI runtime work.
- Do not infer or invent missing fields/routes/widgets from this file. Treat
  the tables below as routing and acceptance constraints for the named follow-up
  tasks.

## Source Map

| Source | Primary role | Required use |
| --- | --- | --- |
| `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md` | Strategy Workshop product contract. Defines expert long-description intake, first servant response, Strategy Reconstruction Card, 12 strategy completeness blocks, high-value questioning, Winner Branch research/decomposition/backtest flow, and join-Trading-Room readiness. | AG-FE-DYNUI-001 and backend workshop follow-ups must treat the workshop as an event/card-driven co-construction workspace, not a chat page or StrategySpec form. |
| `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md` | Trading Room dynamic workspace contract. Defines generation progress, `TradingRoomWorkspaceProposal`, 7 Winner Branch views, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, widget edit actions, `WidgetRevisionProposal`, versions, change log, rollback, and BFF route family. | AG-BE-DYNUI-001/002/003, AG-XR-DYNUI-001, and AG-FE-DYNUI-002/003/004 must implement this as generated dynamic state, not a static dashboard. |
| `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md` | Multi-strategy dashboard and personalization contract. Defines three main workspaces, Strategy Lens switcher, structurally distinct dashboard architecture per lens, Dashboard Control Bar, Before/After proposal, widget menu, change log, personalization, and forbidden Management/runtime words. | Use for cross-lens layout identity and personalization expectations. It supports V11 but does not replace V11's Winner Branch-specific workspace proposal. |
| `uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md` | Dashboard-control interaction contract. Defines natural-language assistant adjustment, proposal drawer, Before/After preview, Widget Proposal, widget menu, change log, personalization status, and validator/code-injection safety. | Use for the assistant-adjustment UX and safety boundary. V11 narrows this into widget-level Trading Room revision flow. |
| `Agora.dc.html` | Dynamic prototype carrying concrete UI states. Key line clusters in the extracted file: global AGORA shell and assistant command bar around lines 34-42; main tabs around 110-111; dashboard adjust/change-log hooks around 160 and 269; V11 generation/proposal/active Trading Room around 619-715; V10 Strategy Workshop around 825-1000; change log/adjust drawer around 1197-1294; add-widget/revision/version drawers around 1305-1385; state model and seeded Winner Branch views/widgets around 1412-2158. | Use as interaction/state reference for visual parity and dynamic state sequencing. Do not treat it as production code or as permission to inject arbitrary HTML/JS. |
| `screenshots/01-v10-mid.png` | 924x540 JPEG payload with `.png` filename. Strategy Workshop mid-state: dark AGORA shell, Strategy Workshop tab, long Winner Branch description card, right-side 12-block completeness rail, bottom servant composer, and join/readiness context. | Primary visual reference for V10 workshop layout and "not a chat page" density. |
| `screenshots/02-v10-mid.png` | 924x540 JPEG payload with `.png` filename. Same V10 mid-state reference in the archive. | Treat as duplicate/alternate capture for V10 mid-state unless later design review distinguishes it. |
| `screenshots/01-applied.png` | 924x540 JPEG payload with `.png` filename. Dashboard adjustment proposal drawer after natural-language request: quick intent chips, assistant understanding, proposed changes, and apply/reject actions over the trading desk. | Primary visual reference for V4/V6 assistant dashboard proposal UX. |
| `screenshots/01-aifix.png` | 924x540 JPEG payload with `.png` filename. Trading desk with command bar request, AI drawer/progress overlay, candidate table, and lens context. | Primary visual reference for command-to-context AI flow and non-static desk shell. |

Additional screenshots in the same archive (`drawer.png`, `adjust.png`,
`directions.png`, `dashB.png`, `dashB2.png`, `01/02-adjust2.png`,
`01/02-applied.png`, `01/02-ai*.png`, `v5-workshop.png`, `v5-signals.png`)
are supporting visual references for drawers, directions, and dashboard
variants. The four screenshots above are the task brief's required primary
references.

## Current Implementation Map

### Backend / Contract Surfaces

| Surface | Current artifact | Current state | Gap against V10/V11 |
| --- | --- | --- | --- |
| Strategy workshop session | `services/control-plane/specs/agora/strategy_workshop.schema.json` | Basic workshop identity/lifecycle, subject, participants, completeness refs, research plan refs. | Generic session schema only; does not encode V10 first-response sequencing or the 12 Winner Branch completeness blocks. |
| Workshop events | `services/control-plane/specs/agora/v3/workshop_event.schema.json`, `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | Event envelope and typed SSE event enum exist for messages, servant responses, completeness, readiness, research, patches, versions, snapshots. | Payload is still generic for several event types; V10 "first servant response inserts Strategy Reconstruction Card" is not contractually enforced. |
| Workshop cards | `services/control-plane/specs/agora/v4/workshop_card.schema.json` | Typed card envelope includes `servant_reconstruction`, `completeness_update`, `next_question`, research cards, version patch, version compare, readiness gate. | Good foundation, but V10 card payload does not guarantee all V10 sections: strategy core, subquestions, recognized components, legal/research limitation labels, and 12-block status categories must be explicitly produced by runtime and rendered by FE. |
| Strategy completeness | `services/control-plane/specs/agora/strategy_completeness.schema.json` | Seven generic dimensions: hypothesis, data dependencies, market scope, evaluation plan, risk constraints, execution profile, governance. | V10 requires 12 named blocks: market scope, insider/branch mapping, winner branch scoring, migration/reverse flow, event lead, signal formation, entry/holding, add/reduce/exit, sizing/leverage, cost/liquidity/capacity, validation/backtest/refutation, monitoring/update. |
| Readiness gate | `services/control-plane/specs/agora/v4/strategy_readiness.schema.json` | Three gates exist: preliminary research, full validation, trading room. | Join-to-Trading-Room readiness can be represented, but there is no downstream V11 proposal-generation route bound to the ready gate yet. |
| Dashboard recipe v2 | `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` and `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Per-user/per-strategy recipe with views, placements, widgets, status proposal/active/archive/rolled_back, append-only versions, propose/accept/layout/rollback/versions routes. | This is a dashboard recipe proposal/version system, not the full V11 `TradingRoomWorkspaceProposal`/workspace lifecycle. It lacks proposal-level rationale, per-view thumbnails/counts, data availability, warnings, personalization summary, active workspace state, and widget revision proposal resources. |
| Widget and chart specs | `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json`, `services/control-plane/specs/agora/v2/chart_spec_v1.schema.json`, `services/control-plane/specs/agora/widget_registry.v1.json` | Safe `WidgetSpecV2`, `ChartSpecV1`, interaction enum, blocked interaction policy in frontend, and 42 active registry entries including Winner Branch widgets. | Strong allowlist foundation. Missing V11 `TradingRoomWidgetSpec` naming/context fields such as `purpose`, `whyIncluded`, placement min/preferred/max dimensions, and widget-context request payloads for servant adjustment. |
| Trading Room aggregate | `services/control-plane/specs/agora/v4/trading_room_aggregate.schema.json`, `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Read aggregate, strategy detail, decision events, decision recording, and Trading Room SSE. Explicitly read-only/no order routing except governed request-only intent handoff. | Does not include V11 proposal endpoints, workspace endpoints, layout PATCH, view mutation, widget mutation, widget revision proposal endpoints, workspace versions, or rollback endpoints. |
| Safety / no order route | `services/control-plane/openapi/agora_v1_3.openapi.yaml`, `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`, widget registry validator surfaces | Agora decision events are request-only; `no_order_route_proof` exists; widget validation and allowlists exist. | All new V11 routes must preserve this boundary: no live order, no capital binding, no Management/RuntimeBinding/broker UI language, no arbitrary React/JS/HTML injection. |

### Frontend Surfaces

| Surface | Current artifact | Current state | Gap against V10/V11 |
| --- | --- | --- | --- |
| Strategy Workshop page | `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` | Loads workshop, completeness, readiness, cards; subscribes to workshop SSE; renders card stream and right rail; disables Add to Trading Room until `highest_ready_gate === "trading_room"`. | Current UX is a light skeleton and does not enforce V10 first response order, V10 dark AGORA layout, V10 Chinese servant language, or the exact 12-block right rail. Add-to-Trading-Room has no generation/proposal handoff. |
| Workshop cards | `execute-plans/src/agora/components/WorkshopCardRenderer.tsx` and card components | Renders typed servant reconstruction, research, consult, backtest, version compare, readiness cards. | The reconstruction card is a generic causal chain; V10 requires explicit sections for core, subquestions, components, limitations, and high-value follow-up decisions. |
| Strategy completeness rail | `execute-plans/src/agora/components/StrategyCompletenessRail.tsx` | Renders generic completeness dimensions, readiness gates, and next high-value question. | Needs V10 12-block labels/states: confirmed, servant-inferred, missing, weak, conflict. |
| Trading Room page | `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Renders aggregate strategy list, risk/queue strips, decision event queue, and strategy recipe workspace when `dashboard_recipe_id` exists. | Does not implement V11 join-generation progress, complete workspace proposal preview, active workspace shell from `TradingRoomWorkspace`, per-view proposal thumbnails/counts, or workspace versions. |
| Grid editor | `execute-plans/src/agora/dashboard/DashboardGridEditor.tsx` | Uses `react-grid-layout`; supports drag/resize via placements, add widget panel, remove button, change chart panel, personalization event callback. | In `TradingRoomPage`, persistence callbacks are currently no-ops. Missing V11 save/discard unsaved changes, restore removed widget library, duplicate, fullscreen, edit-mode shell, and PATCH integration. |
| Proposal preview | `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx` | Compares active/proposed `DashboardRecipeV2`; supports accept/reject/keep-both request envelopes and before/after widget previews. | Useful component, but not yet wired to V11 `TradingRoomWorkspaceProposal`; recipe delta is not enough to satisfy generated workspace proposal acceptance. |
| Change log / rollback | `execute-plans/src/agora/dashboard/DashboardChangeLog.tsx` | Displays dashboard recipe versions and can request rollback with ETag/idempotency. | Needs Trading Room workspace version/change log/rollback, scoped per trader, strategy, and strategy version, not only dashboard recipe versions. |
| Widget renderer and registry | `execute-plans/src/agora/widgets/*`, `execute-plans/src/agora/widgets/registry.ts` | Registry-backed rendering, ChartSpec validation grammar, blocked interaction list, `request_widget_revision` interaction kind. | Must be wired to full widget-context-aware servant adjustment and backend revision proposal lifecycle. |
| Widget revision drawer | `execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx` | Local drawer supports instruction, request callback, validation, before/after preview, accept, keep both, reject. | Missing backend `WidgetRevisionProposal` resource/status, before/after persistence, widget context envelope, adjust-again path, and application to workspace version history. |

## Dynamic Invariants

These invariants are acceptance blockers for all downstream dynamic UI tasks.
Any implementation that only hardcodes cards, screenshots, or one-off mock state
fails this source map.

1. Strategy Workshop V10 is a strategy co-construction workspace. It is not a
   generic chat page, survey, static form, or raw StrategySpec editor.
2. A long strategy description must first yield a Strategy Reconstruction Card.
   The first servant response cannot immediately ask generic questions.
3. The Strategy Reconstruction Card must show at least the V10-required
   strategy core, derived research subquestions, recognized components, and
   research/legal limitation labeling for Winner Branch style hypotheses.
4. The right rail must track the V10 12 strategy blocks and must distinguish
   confirmed, servant-inferred, missing, weak, and conflict states.
5. Readiness to join the Trading Room must be data-driven through readiness
   gates. A disabled/enabled button alone is insufficient unless it triggers
   the V11 proposal-generation flow.
6. Joining the Trading Room must first create a complete
   `TradingRoomWorkspaceProposal`. The trader must not land in an empty
   dashboard or a prebuilt static skeleton.
7. A Trading Room proposal must include generated views, thumbnails or equivalent
   previews, widget counts, rationale, data availability, warnings, and
   personalization applied.
8. Winner Branch V11 requires at least these generated views: strategy overview,
   candidates/entry, winner branch intelligence, related-party/flow migration,
   event lead, positions/add/reduce/exit, and evidence/monitoring rules.
9. Widgets must be declarative specs validated by schema and registry. Agents
   cannot inject arbitrary React, JavaScript, HTML, external scripts, raw prompts,
   unsupported data sources, cross-user data, live order actions, or broker
   control actions.
10. Widgets must be editable through controlled actions: drag, resize, remove,
    restore, add, duplicate, change chart, save/discard, and reset/rollback.
11. Removing a widget hides it from the current view; it must not delete the
    underlying data, history, or restore path.
12. Clicking or menu-selecting a widget must open a servant adjustment flow with
    widget purpose, data source, fields, filters, time window, chart type,
    strategy/view/evidence context, and current placement.
13. The servant cannot directly mutate a widget. It must return a
    `WidgetRevisionProposal` with before spec, proposed spec, rationale,
    warnings, data availability, validation result, and status.
14. The revision UX must support apply, adjust again, keep original and add a
    modified copy, and cancel.
15. Workspace versions are scoped per trader, per strategy, and per strategy
    version. Change log and rollback are required functionality.
16. Trading decisions in Agora remain decision-support/request-only. Agora must
    not expose direct order routing, capital binding, RuntimeBinding,
    Management, broker backend language, or live execution controls.
17. Visual parity work must sit on top of dynamic contracts/runtime. It cannot
    be delivered by recreating `Agora.dc.html` as a static page.

## Gap Routing

| Gap | Owner task route |
| --- | --- |
| Missing `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, and workspace proposal routes | AG-BE-DYNUI-001 |
| Missing backend `WidgetRevisionProposal`, accept/keep-copy/cancel, workspace versions, change log, rollback | AG-BE-DYNUI-002 |
| Missing trading servant generator that produces full Winner Branch workspace proposals through validator | AG-BE-DYNUI-003 |
| Missing OpenAPI/generated frontend types for the dynamic Trading Room contract family | AG-XR-DYNUI-001 |
| V10 first-response runtime, Strategy Reconstruction Card sections, 12-block rail, readiness-driven join handoff | AG-FE-DYNUI-001 |
| V11 generation progress, workspace proposal preview, view thumbnails/counts, accept-to-workspace shell | AG-FE-DYNUI-002 |
| V11 persisted grid editor, add/remove/restore/duplicate/change chart/save-discard/personalization | AG-FE-DYNUI-003 |
| V11 widget-context adjustment drawer and backend-backed before/after revision proposal flow | AG-FE-DYNUI-004 |
| Dark AGORA visual parity from screenshots/prototype after dynamic runtime exists | AG-FE-DYNUI-005 |
| Full Winner Branch dynamic UI E2E acceptance | AG-E2E-DYNUI-001 |

## Non-Static Acceptance Guard

Reviewers should reject any downstream delivery that:

- uses screenshots or static mock cards as the primary implementation;
- skips proposal generation and opens an empty dashboard;
- hardcodes Winner Branch widgets without `WidgetSpec`/`ChartSpec` validation;
- treats `DashboardRecipeV2` alone as the whole V11 workspace proposal;
- wires widget revision UI without a proposal object and before/after preview;
- exposes Management/runtime/broker/capital-binding language in Agora;
- claims completion without version history and rollback evidence.
