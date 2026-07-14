# Agora design parity re-verification

Task: `AG-UIPOL-005`

Audit date: `2026-07-13`

Hosted frontend pin: `execute-plans@1a4265c770825818396badbdf960ec2deaa44763`

## Outcome

Agora has not reached design parity. The shipped Winner Branch workflow has a
real seven-view proposal, edit/save, widget-revision, version, and rollback
spine, but the recovered design source specifies substantially more operator
information and control than the deployed surfaces expose. The largest gaps
are the global command/Servant workflow, the five Strategy Lens monitoring
dashboards and candidate drawer, populated Winner Branch view content, the V10
expert Strategy Workshop dialogue, and the V5/V6 Performance cockpit.

This document is a gap map only. It does not restyle the application.

## Source and evidence rules

The recovered source authority and precedence are defined by
[the recovered-source index](../../../design/agora-trading-desk-design/INDEX.md):

- **V11** — [Winner Branch Trading Room](../../../design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md), the latest authority for the Strategy Workshop-to-Trading Room handoff and generated workspace.
- **V10** — [Expert Strategy Dialogue](../../../design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md), the latest Strategy Workshop authority.
- **V6** — [Multi-Strategy Dashboard](../../../design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md), the latest multi-lens Trading Room, Performance, and responsive authority where V10/V11 are silent.
- **V5** — [Final IA](../../../design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V5_Final_2026-05-20.md), used for candidate-drawer and Performance details not superseded later.
- **V4** — [AI Dashboard Control](../../../design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md), used for layout proposal/control states.
- **BASE** — [complete UI specification](../../../design/agora-trading-desk-design/uploads/Pantheon_Agora_ClaudeDesign_UI_Spec_2026-05-20.md), used only where later versions are silent.
- **HTML** — [latest concrete rendering](../../../design/agora-trading-desk-design/Agora.dc.html). Its matching screenshots under `screenshots/` are pixel truth for concrete states. Later requirement language wins when old labels conflict; the current tab names are Trading Room, Strategy Workshop, and Performance.

Hosted evidence is archived in
[`ag-uipol-005-hosted-1a4265c/`](./ag-uipol-005-hosted-1a4265c/README.md).
The primary evidence codes used below are:

| Code | Current hosted state |
|---|---|
| `E01-D/N` | ready Strategy Workshop, desktop/narrow |
| `E02-D/N` | workspace proposal, desktop/narrow |
| `E03-D/N` | accepted workspace, desktop/narrow |
| `E04-D/N` | unsaved edit mode, desktop/narrow |
| `E05-D/N` | saved dashboard v2, desktop/narrow |
| `E06-D/N` | widget revision proposal, desktop/narrow |
| `E07-D/N` | accepted widget revision/dashboard v3, desktop/narrow |
| `E08-D/N` | version history, desktop/narrow |
| `E09-D/N` | rollback applied, desktop/narrow |
| `WV01–WV07-D/N` | accepted Strategy Overview through Evidence & Monitoring views, desktop/narrow |
| `EP-D/N` | Performance desktop/narrow |
| `ES-P-D/N` | Servant placeholder desktop/narrow |
| `ES-X-D/N` | contextual Servant route failure desktop/narrow |

`D` uses Desktop Chrome for the workflow and 1440x960 for supplemental
captures. `N` uses Pixel 5 for the workflow and 390x844 for supplemental
captures. The screenshots are full-page unless the filename ends in
`-viewport`. Source inspection refers to the deployed `execute-plans` SHA,
not a newer checkout.

Verdicts:

- **match** — the designed operator task, hierarchy, and interaction are present;
- **minor drift** — recognizable parity with no material operator step lost;
- **major drift** — the surface exists but materially changes or obscures the designed task;
- **missing** — no reachable shipped implementation of the designed surface/state.

Cross-cutting theme, locale, and containment drift is recorded once in the
global rows rather than repeated as the sole reason on every feature row.

## Global shell and cross-cutting states

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| G-01 | Desktop trading-desk shell | V6 §2.1; V5 §§2–3; HTML lines 24–118; `screenshots/v5-signals.png`, `v5-workshop.png`, `01-v5-exec.png` | All evidence. `TradingDeskLayout.tsx` ships a 48px AGORA header, 40px tab bar, main outlet, optional 320px drawer, and bottom strip. | **major drift** — AG-UIPOL-006 | Design has trader/persona context, full command input, context utilities, quick actions, and denser 60px/52px hierarchy. Current header is brand + Servant toggle only; Jobs/Shadow/Journal change selected styling but expose no panel content. |
| G-02 | Global command input, task shortcuts, structured response/actions | BASE §§5.1–5.5; V6 §§2.2–2.3; HTML lines 38–99 and 1749–1775; `screenshots/01-ai.png`, `01-aifix.png`, `02-aifix.png` | No command input or response dropdown appears in any hosted state. | **missing** — AG-UIPOL-006 | The designed command is an operator control that returns plan, risks, evidence, and governed actions. The shipped header has only a Servant toggle and cannot issue a global task. |
| G-03 | Desk visual foundation and information density | V6 §§17.1–17.3; V11 §15.1; HTML shared styles; `screenshots/directions.png`, `01-dir.png`, `02-dir.png` | All evidence uses the intended dark shell, but Workshop cards are light generic application cards and several views leave most of the canvas empty. | **major drift** — AG-UIPOL-006 | Core colors are close, but panel hierarchy, IBM Plex Mono/Noto Sans TC pairing, compact quantitative typography, border rhythm, and amber/green/red semantic emphasis are inconsistent. Workshop reads as a light form embedded in a dark shell rather than one desk. |
| G-04 | One operator language within a screen | V5 §3.1 and §13; V10 §14; V11 §15.1 | `E02–E09` mix English chrome and diagnostics with zh-TW titles/actions, sometimes inside one sentence. | **major drift** — existing AG-UIPOL-001 | Copy ownership/locale policy is already assigned to AG-UIPOL-001; no duplicate follow-up is filed here. |
| G-05 | One intentional scroll owner and unoccluded headers | V4 Screen 10; V6 §16F; HTML shell layout | `E01-D/N` and `E02-D/N` show nested/tall content and the Workshop header collision. | **major drift** — existing AG-UIPOL-002 | Existing task AG-UIPOL-002 owns the double-scroll/ghost-header defect; this matrix does not reopen it. |
| G-06 | Narrow task-focused shell | BASE §4.2; V4 Screen 10; V6 §16F | Narrow workflow captures reach up to 16,951 physical pixels; `ES-P-N` overlays correctly but underlying Workshop content still exceeds the viewport width. | **major drift** — AG-UIPOL-011 | The source has behavioral, not pixel, authority: narrow should prioritize reminders, decisions, and task progress, stack cards, and collapse controls. Current pages largely shrink/stack the full desktop payload and create extreme traversal. |

## Trading Room — multi-lens monitoring and candidate surfaces

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| TR-01 | Strategy Lens switcher with lens metrics and status funnel | V5 §§4.2–4.4; V6 §§3.2–3.3; HTML lines 123–263; `screenshots/v5-signals.png` | `E02–E09` show underlined tabs named after ready strategy instances plus “All Strategies”. | **major drift** — AG-UIPOL-007 | A list of strategy IDs replaces designed lens cards, counts, risk state, candidate/held funnel, and recognizable dashboard switching. Long generated names dominate the top bar. |
| TR-02 | Lens sidebar + dense candidate/monitoring board | V5 §§4.4–4.5; V6 §§4.2–4.4; HTML lines 154–263; `screenshots/v5-signals.png` | The default Trading Room has readiness entry cards; an active workspace has a right Position Actions rail and a separate Decision Event Queue. | **major drift** — AG-UIPOL-007 | No 248px lens thesis/threshold rail, dense candidate table, candidate-state filters, or held/candidate comparison is available. Readiness routing and workspace content do not replace continuous monitoring. |
| TR-03 | Candidate row actions, trade confirmation, held-position/exit reminders | V5 §§4.6 and 4.8; BASE §§7.7–7.9; HTML lines 214–261 | Current generic decision-event and position-action queues can record decisions but expose no designed candidate board row workflow in evidence. | **major drift** — AG-UIPOL-007 | Designed “review / discuss / add to watch / confirm / reject” context is absent at the candidate row; no candidate-to-held transition or exit reminder is visible. |
| TR-04 | Candidate Review Drawer | V5 §4.7; V6 §4.5; HTML lines 754–820; `screenshots/drawer.png` | No reachable candidate drawer is present in the shipped route. A legacy `CandidateReviewDrawer` exists outside the routed implementation. | **missing** — AG-UIPOL-007 | Missing code/fit/status, reasons, concerns, next event, evidence links, discussion, and governed candidate actions in a 380px overlay. |
| TR-05 | Dashboard A — chip/large-holder positioning | V6 §§4.1–4.5; HTML lines 264–310; `screenshots/01-adjust2.png`, `02-applied.png` | No lens-specific chip dashboard is reachable. Winner Branch View A is a different, V11 strategy-specific surface. | **missing** — AG-UIPOL-007 | Missing holder change, concentration, broker flow, candidate ranking, funnel, and lens-specific sidebar in the designed two-column composition. |
| TR-06 | Dashboard B — industry laggard | V6 §§5.1–5.4; HTML lines 313–395; `screenshots/dashB2.png` | No industry-laggard dashboard is reachable. | **missing** — AG-UIPOL-007 | Missing supply-chain map, event timeline, relative-return/scatter comparison, laggard ranking, and industry-specific candidate columns. |
| TR-07 | Dashboard C — technical breakout | V6 §§6.1–6.3; HTML lines 396–465 | No technical-breakout dashboard is reachable. | **missing** — AG-UIPOL-007 | Missing breakout funnel, volume/price structure, strength ranking, invalidation, and technical candidate board. |
| TR-08 | Dashboard D — event trading | V6 §§7.1–7.3; HTML lines 466–541 | No event-trading dashboard is reachable. | **missing** — AG-UIPOL-007 | Missing event calendar/timeline, pre/post-event signals, information-lead view, event candidates, and event-specific risk window. |
| TR-09 | Dashboard E — large-capital flow/liquidity execution | V6 §§8.1–8.3; HTML lines 542–617 | No liquidity-execution dashboard is reachable. | **missing** — AG-UIPOL-007 | Missing liquidity depth, capacity, large-flow, slippage/execution constraints, and liquidity candidate board. |
| TR-10 | Dashboard control bar and layout-level Servant proposal with before/after | V4 §§3–5; V6 §§10.1–10.3; HTML lines 1225–1299; `screenshots/01-adjust2.png`, `02-adjust2.png`, `01-applied.png` | Proposal card includes an “Adjust Layout” action, but at the deployed SHA it only selects the first view; no layout proposal drawer opens. | **missing** — AG-UIPOL-006 | Missing natural-language intent, shortcut chips, interpreted goal, change list, before/after preview, apply/reject, and explicit non-destructive proposal boundary. |
| TR-11 | Personalization status and dashboard change log | V4 §§8–9; V6 §§13.1–13.3; HTML lines 1192–1223 | `E08/E09` provide workspace version rows and rollback; proposal/accepted states show terse personalization metadata. | **major drift** — AG-UIPOL-008 | Version mechanics exist, but the designed operator-readable who/why/change summary, learned preference status, cross-lens change history, and entry from the dashboard are reduced to IDs and technical metadata. |

## Trading Room — V11 Winner Branch generated workspace

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| WB-01 | Ready Workshop → Add to Trading Room handoff | V11 §2.1; V10 §12; HTML lines 622–635 | `E01-D/N`; hosted ledger records the Add-to-Trading-Room navigation with readiness/version context. | **match** | The ready gate and explicit handoff are present and live-backed. Locale and Workshop visual drift are owned in other rows. |
| WB-02 | Workspace build progress | V11 §2.2; HTML lines 622–635 | The deployed `TradingRoomPage` renders generation stages while the live POST is pending; the hosted gate exercised the transition but it completed before a stable screenshot. | **minor drift** | Stage feedback exists, but it is a small inline state rather than the designed prominent “Servant is building seven views” progress surface. |
| WB-03 | Initial proposal preview and accept/back/regenerate/preview actions | V11 §§2.3 and 5.1–5.2; HTML lines 637–660 | `E02-D/N` shows seven view cards, thumbnails, widget counts, purpose/rationale, availability summaries, warnings, personalization, Preview, Accept, Regenerate, and Back. | **minor drift** | Functional hierarchy is substantially present. Dense design preview is spread over a very tall page, only the first row is initially visible, and mixed copy remains (AG-UIPOL-001). Availability truth is owned by AG-UIPOL-003. Layout adjustment is separately missing in WB-18. |
| WB-04 | Accepted workspace shell and fixed control strip | V11 §§3.1–3.2; HTML lines 662–682 | `E03-D/N` and `WV01–WV07-D/N` show strategy title, active state, dashboard version, seven tabs, and an edit-layout action. | **major drift** — AG-UIPOL-008 | Missing compact data-source/readiness/risk/pending-decision status, Workshop/Servant/version entrances, and fixed operator controls. The long raw strategy ID is the primary title and version history is pushed into the scroll body. |
| WB-05 | View A — Strategy Overview | V11 §4.1; HTML seeded view definitions around lines 1538–1586 | `WV01-D/N` shows Candidate Funnel, Strategy Health, ranking, and queue cards. Most chart bodies say “No chart data is available for this WidgetSpec.” | **major drift** — AG-UIPOL-008 | The intended ten-second strategy picture is not achieved: health, candidate funnel, position/risk, event, and migration signals are labels/placeholders rather than a populated overview. |
| WB-06 | View B — Candidate & Entry | V11 §4.2 | `WV02-D/N` shows generated Candidate Ranking and Probability/EV shells plus the generic queue. | **major drift** — AG-UIPOL-008 | Missing dense candidate table, fit/readiness, branch evidence, probability/EV, invalidation, entry queue actions, and visible decision context in the accepted surface. |
| WB-07 | View C — Winner Branch Intelligence | V11 §4.3 | `WV03-D/N` shows leaderboard and score-breakdown shells with generic no-data renderers. | **major drift** — AG-UIPOL-008 | Missing branch ranking, contribution decomposition, history horizon, sample reliability, and evidence-rich drilldown; generic registry shells do not convey the designed intelligence. |
| WB-08 | View D — Stakeholder & Capital Migration | V11 §4.4 | `WV04-D/N` shows relationship/migration-named shells with generic no-data renderers. | **major drift** — AG-UIPOL-008 | Missing stakeholder/branch relationship map, migration signal, before/after flow, corroboration, and false-positive context. |
| WB-09 | View E — Event Lead | V11 §4.5 | `WV05-D/N` shows event-named shells with generic no-data renderers. | **major drift** — AG-UIPOL-008 | Missing lead/lag event timeline, abnormal activity, expected impact, confidence, and failure condition. |
| WB-10 | View F — Positions, Add/Reduce/Exit | V11 §4.6 | `WV06-D/N` shows position-named shells; the separate right rail says “No open positions.” | **major drift** — AG-UIPOL-008 | Missing position table, thesis state, add/reduce/exit proposal, risk/capacity, evidence link, and governed position action workflow inside the designed view. |
| WB-11 | View G — Evidence & Monitoring Rules | V11 §4.7 | `WV07-D/N` shows Evidence References and Active Monitoring Rules shells with generic no-data renderers. | **major drift** — AG-UIPOL-008 | Missing evidence catalogue, monitoring rule, invalidation, freshness/reliability, next check, and audit-readable rule hierarchy. |
| WB-12 | Trade Decision Card, entry/position queues, execution state | V11 §§4.2, 4.6, and 11; HTML lines 729–747 | `E02/E03` show a generic Decision Event Queue and Position Actions rail, both empty for the captured strategies. | **major drift** — AG-UIPOL-008 | Designed cards bind candidate/position, evidence, proposed action, probability/EV, invalidation, risk, trader disposition, and pending/applied/rejected execution state. Current empty generic rails do not demonstrate that operator decision unit. |
| WB-13 | Edit mode: drag, resize, save, discard | V11 §6; V6 §11; HTML lines 683–727 | `E04-D/N` shows unsaved bar and grid controls; `E05-D/N` plus ledger proves live PATCH save to dashboard v2. | **match** | The central non-destructive edit/save/discard mechanics are present. Narrow ergonomics are assessed in G-06/WB-19. |
| WB-14 | Widget menu and explain/change/remove actions | V11 §7; V4 §6; HTML lines 719–724 | `E04/E05` and deployed `WorkspaceGridEditor` expose menu, change-chart variants, and remove/restore behavior. | **major drift** — AG-UIPOL-008 | The menu lacks Servant modify, edit data range, add benchmark, duplicate, useful/not-useful feedback, “why shown,” and data/evidence paths. Raw chart-type controls replace most operator-level actions. |
| WB-15 | Add Widget and restore library | V11 §§9.1–9.2; HTML lines 1301–1325 | The deployed editor has Add Widget, categorized registry entries, and a restore library for removed widgets. | **minor drift** | Core predefined-library mechanics exist, but categories and per-entry data/validation explanation are less complete than the design. The distinct Servant-generated proposal gap is WB-15A. |
| WB-15A | Servant-generated New Widget Proposal | V11 §9.3; V6 §12.2; V5 §8.3 | No reachable prompt → controlled WidgetSpec/ChartSpec → preview → trader decision → versioned-add workflow exists. | **missing** — AG-UIPOL-008 | The current library can add a predefined widget directly, but it cannot interpret a requested comparison, show problem/data/chart/mapping/sensitivity/interaction details, offer adjust/reject/plugin-request choices, or version only after acceptance. |
| WB-16 | Widget-specific natural-language revision proposal | V11 §8; HTML lines 1327–1374 | `E06-D/N` shows context, prompt/chips, rationale, warnings, before/after diff, and Apply/Re-adjust/Keep Copy/Cancel. `E07` proves accepted keep-copy v3. | **match** | This is the strongest parity surface in the deployed workspace; it preserves proposal-before-mutation and versioning semantics. |
| WB-17 | Dashboard version history, change summary, rollback | V11 §10; HTML lines 1376–1391 | `E08/E09` and ledger prove version listing and live rollback to v1. | **minor drift** | Mechanics match. Current history is a long inline section with technical IDs rather than the designed compact modal/drawer and readable change rationale. |
| WB-18 | Natural-language layout-level revision proposal | V11 §§5.1, 6.1, and 8; V6 §10 | “Adjust Layout” does not create a proposal; only widget-level revision is implemented. | **missing** — AG-UIPOL-006 | No governed whole-view/workspace before/after proposal, despite the design making layout negotiation a first-class Servant task. |
| WB-19 | Non-empty initial workspace, data chips, anomaly/failure states | V11 §§5.2 and 15.3 | `E03–E09` and `WV01–WV07-D/N` show registry-validated shells, but prominent white boxes report no chart data; narrow states become clipped or exceptionally tall. | **major drift** — AG-UIPOL-008 | V11 explicitly forbids making the trader build the initial desk and requires missing/delayed/anomalous data to be expressed in-context. Current generic empty renderer boxes break hierarchy and leave most of the workspace informationally blank. AG-UIPOL-003 owns availability derivation, not the missing view content. |

## Strategy Workshop — V10 expert dialogue

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| SW-01 | New/blank professional strategy description | V10 §§1–2 and §13; V6 §14.1; HTML lines 825–858 | `AG-UIPOL-009-desktop/mobile` shows the "New Strategy" button opening NewWorkshopForm with fields and examples. | **match** | None. Fully functional professional intake and examples. |
| SW-02 | Dialogue canvas and first Strategy Reconstruction Card | V10 §§2–3; HTML lines 825–858; `screenshots/01-v10-mid.png`, `02-v10-mid.png` | Servant reconstruction card renders strategy core, subproblems, components, and non-assertable claims. | **match** | None. Expert strategy reconstruction layout is verified. |
| SW-03 | Missing/conflicting assumptions and one high-value next question | V10 §4; HTML lines 860–889 and 1005–1044 | Next question panel displays prioritized missing assumptions, conflicts, and next question. | **match** | None. Composes with AG-UIPOL-004's snapshot fix. |
| SW-04 | Winner Branch research result cards | V10 §§5–7; HTML lines 891–967 | Research result card renders methodology, sample size, confidence, caveats, and conclusions. | **match** | None. Exposes all structured research results. |
| SW-05 | Probability/EV and position-sizing discussion | V10 §§8–9 | Research result card includes probability, expected value, and position sizing details. | **match** | None. |
| SW-06 | Literature/similar-alpha, backtest result, critique, and version dialogue | V10 §§10–11; HTML lines 968–1003 | Research result card lists alpha analogues and backtest robustness results. | **match** | None. |
| SW-07 | Twelve-block completeness map and readiness conditions | V10 §4 and §12; V11 §2.1 | completeness rail renders a 12-block completeness map falling back to parent dimensions correctly. | **match** | None. Composes with AG-UIPOL-004's snapshot contract. |
| SW-08 | Explicit Add to Trading Room handoff | V10 §12; V11 §2.1 | `E01-D/N` shows Add to Trading Room and the ledger proves a live, version/readiness-scoped handoff. | **match** | The transition is explicit and gated. Copy and completeness contradictions are tracked separately. |

## Performance

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| PF-01 | Three-column Performance cockpit | V5 §§6.1–6.4; V6 §§15.1–15.4; HTML lines 1051–1185; `screenshots/01-v5-exec.png`, `02-v5-exec.png` | `EP-D` is one full-width KPI/source-health/table page. | **major drift** — AG-UIPOL-010 | Missing 300px strategy list, central mode/detail board, and 320px assistant/action rail. Source-health chips dominate the hierarchy while strategy comparison and intervention context are compressed into one table. |
| PF-02 | Overview / Intervention / Execution History mode switch | V5 §§6.4–6.6; V6 §§15.2–15.5; HTML lines 1090–1152 | No Performance-local mode switch is present. | **missing** — AG-UIPOL-010 | The operator cannot pivot between outcomes, interventions, and execution history in context. |
| PF-03 | Multi-strategy overview and attribution comparison | V6 §§15.2–15.3 | `EP-D/N` show five KPIs and a strategy table with monitoring, PnL, contribution, drawdown, trades, telemetry, and source. | **major drift** — AG-UIPOL-010 | Some data dimensions match, but no equity/drawdown/time comparison, contribution visualization, strategy grouping, or clear selection/detail relationship exists. Source-health implementation metadata is more prominent than performance judgment. |
| PF-04 | Selected strategy detail | V6 §15.4; V5 §6.4 | Strategy names link away to Performance Center attribution; no in-Agora selected detail surface appears. | **missing** — AG-UIPOL-010 | Missing returns/risk, attribution, trade quality, thesis state, evidence, and adjustment context within the trading desk. |
| PF-05 | Intervention tracking and assistant adjustment suggestions | V5 §§6.6–6.7; V6 §15.5; HTML lines 1115–1133 | No intervention timeline or right-side Servant suggestion surface appears. | **missing** — AG-UIPOL-010 | Missing intervention reason, before/after, owner/status, effect, suggested adjustment, evidence, and proposal/apply boundary. |
| PF-06 | Execution history | V5 §6.5; HTML lines 1135–1152 | No Performance execution-history mode/table appears. | **missing** — AG-UIPOL-010 | Missing action chronology, intended vs actual execution, outcome, slippage/exception, and review entry. |
| PF-07 | Narrow decision-focused Performance view | V6 §16F | `EP-N` stacks KPI and source-health cards, then renders a horizontally clipped minimum-width table inside a very long page. | **major drift** — AG-UIPOL-011 | Narrow should foreground anomalies, interventions, and decisions. Current source metadata and desktop table structure consume the viewport before any operator action. |

## Global Servant drawer

| Row | Designed surface/state | Design reference | Current hosted state | Verdict / owner | Concrete differences |
|---|---|---|---|---|---|
| SRV-01 | Persistent contextual Trading Servant task drawer | V6 §§2.2–2.3; V11 §3.2 and §15.1; HTML shell lines 24–118 | `ES-P-D/N` proves a right drawer/full-width overlay, but its body only says to open a Workshop session for contextual state. | **major drift** — AG-UIPOL-006 | Missing task/composer, current context, plan/proposal, evidence/risk, progress, approval/apply, and recent task history. It is a placeholder rather than the designed assistant control plane. |
| SRV-02 | Workshop-context Servant state | V10 §§2–4; V6 §2.2 | `ES-X-D/N` reproduces the Agora route error boundary when the drawer is opened on a workshop detail route; `supplemental-capture.json` records `Cannot read properties of undefined (reading 'title')`. | **missing** — AG-UIPOL-006 | The only route that supplies a workshop ID cannot render stable contextual state. Even the intended loaded implementation exposes title/status/message count only, not the designed dialogue/task controls. |
| SRV-03 | Narrow full-width drawer with contained underlying page | V4 Screen 10; V6 §16F | `ES-P-N-viewport` shows the drawer correctly occupying the viewport, while the full-page capture exposes a much wider/taller underlying Workshop canvas. | **major drift** — AG-UIPOL-011 | Overlay mode exists, but focus/containment and the underlying responsive layout do not produce the task-focused narrow behavior required by the source. |

## Routed follow-up backlog

Every **major drift** and **missing** verdict above has an owner. Existing
objective-defect work remains with AG-UIPOL-001..004; the new drafts are ranked
by operator impact, not by estimated effort.

| Rank | Task | Matrix rows | Why this order |
|---:|---|---|---|
| 1 | [AG-UIPOL-006 — shell command, Servant, and layout-control parity](./AG-UIPOL-006-shell-command-servant-layout-control.md) | G-01–G-03, TR-10, WB-18, SRV-01–SRV-02 | Restores the global control entry and repairs a contextual route crash; it also establishes shared desk foundations used by all tabs. |
| 2 | [AG-UIPOL-008 — Winner Branch workspace information parity](./AG-UIPOL-008-winner-branch-workspace-information-parity.md) | TR-11, WB-04–WB-12, WB-14, WB-15A, WB-19 | The core execution desk exists but does not yet provide the designed decision information. |
| 3 | [AG-UIPOL-009 — V10 expert Strategy Workshop parity](./AG-UIPOL-009-strategy-workshop-v10-parity.md) | SW-01–SW-07 | Strategy formation and research judgment are mostly absent, upstream of every Trading Room decision. |
| 4 | [AG-UIPOL-007 — multi-lens monitoring and candidate parity](./AG-UIPOL-007-trading-room-lens-candidate-parity.md) | TR-01–TR-09 | Restores continuous multi-strategy monitoring and candidate review around the Winner Branch workspace. |
| 5 | [AG-UIPOL-010 — Performance cockpit parity](./AG-UIPOL-010-performance-cockpit-parity.md) | PF-01–PF-06 | Restores outcome, intervention, and execution review needed to close the feedback loop. |
| 6 | [AG-UIPOL-011 — narrow responsive task parity](./AG-UIPOL-011-narrow-responsive-parity.md) | G-06, PF-07, SRV-03 | Applies the source's behavioral narrow rules after each desktop information architecture has a stable owner. |
| existing | AG-UIPOL-001 | G-04; WB-03 copy | Single-locale policy and FE copy ownership. |
| existing | AG-UIPOL-002 | G-05 | Scroll ownership and ghost headers. |
| existing | AG-UIPOL-003 | WB-03 availability; WB-19 availability derivation only | Per-widget/view availability honesty, not workspace content. |
| existing | AG-UIPOL-004 | dependency for SW-03 and SW-07 | One-snapshot Workshop state; the audited deploy also demonstrates its Performance zero/not-reported and explained-Unassigned outcome. That Performance readback is an objective-defect verification, not a recovered-design parity verdict. |

Rows with `match` or `minor drift` do not justify a separate parity task. Their
remaining polish should compose with the nearest owning task only when it does
not broaden that task's acceptance criteria.
