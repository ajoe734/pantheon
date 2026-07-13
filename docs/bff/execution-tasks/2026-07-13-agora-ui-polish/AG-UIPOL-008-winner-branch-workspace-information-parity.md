# AG-UIPOL-008: Winner Branch workspace information parity

Status: draft follow-up from AG-UIPOL-005. Not yet dispatched.

Priority: 2 — the core execution desk exists but is informationally sparse.

## Matrix coverage

`parity-matrix.md` rows TR-11, WB-04–WB-12, WB-14, WB-15A, and WB-19.

## Design authority

- V11 §§3–7, 10–11, and 15
- V6 §§13.1–13.3
- `Agora.dc.html` accepted-workspace, decision-card, personalization, and
  change-log states

## Scope

Complete the information architecture and live content of the accepted
seven-view Winner Branch workspace while preserving the already working
proposal, edit/save, widget revision, version, and rollback spine.

Primary repo: `ajoe734/execute-plans@dev`; additive Pantheon BFF projection
work is in scope where the required live evidence is not exposed.

## Work

1. Build the fixed workspace control strip: human-readable strategy identity,
   readiness/data/risk/pending-decision state, dashboard version, Workshop,
   Servant, version history, and edit entrances.
2. Fill every designed view with its required operator content:
   - A Strategy Overview;
   - B Candidate & Entry;
   - C Winner Branch Intelligence;
   - D Stakeholder & Capital Migration;
   - E Event Lead;
   - F Positions/Add/Reduce/Exit;
   - G Evidence & Monitoring Rules.
3. Replace generic white “No chart data” renderer boxes with designed
   in-context missing/delayed/anomalous states. When data exists, render the
   actual table/chart/evidence hierarchy rather than registry diagnostics.
4. Implement designed Trade Decision Cards and candidate/position queues with
   evidence, probability/EV, invalidation, risk, trader disposition, and honest
   pending/applied/rejected state. Decision support must remain distinct from
   broker execution.
5. Complete the widget menu with Servant modify, data range, benchmark,
   duplicate, useful/not-useful, why-shown, and data/evidence actions while
   retaining current remove/restore and revision behavior.
6. Add the governed New Widget Proposal flow: interpret the trader's request,
   reuse a WidgetSpec or produce controlled ChartSpec, show problem/data/chart/
   mapping/sensitivity/interaction preview, allow adjust/reject/plugin-request,
   and add/version only after acceptance.
7. Make personalization status/change history readable (who/why/change
   summary) while retaining current rollback behavior.

## Non-goals

- Layout-level natural-language proposal (AG-UIPOL-006).
- Availability derivation/badge policy (AG-UIPOL-003); this task consumes it.
- Locale migration (AG-UIPOL-001).
- Multi-lens candidate dashboard outside the V11 workspace (AG-UIPOL-007).

## Acceptance

- Hosted evidence walks all seven accepted views and shows each view's required
  content or an honest, contextual source-specific unavailable state.
- No accepted initial workspace is predominantly blank or filled with generic
  renderer errors; source/validation metadata is subordinate to decisions.
- A live Trade Decision Card demonstrates evidence → trader disposition →
  honest state transition without calling an execution route.
- “Why shown,” the complete operator widget menu, personalization status,
  readable change history, and rollback are reachable from the workspace.
- A live natural-language New Widget request reaches a controlled preview and
  does not alter the workspace until the trader accepts it.
- Contract/component/Playwright tests cover seven-view completeness, missing
  and anomalous data, decision-card state, and preservation of edit/revision/
  rollback behavior.
