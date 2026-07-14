# AG-UIPOL-008 hosted evidence

Captured: 2026-07-14 UTC

## Delivered revisions

- execute-plans PR #331, head commit `1fd86ef6931b11928fb01190e25572bd7e903fdd`
- Pantheon PR #3599
- Hosted FE `/deployment.json`: `1fd86ef6931b11928fb01190e25572bd7e903fdd`

## Verification and Integration Proof

1. **Reactive Header and Control Strip Verification:**
   - Strategy identity and version are dynamically populated.
   - Strategy readiness states reflect backend monitoring values (e.g. `READY`, `MONITORING`).
   - Data freshness indicators successfully render time cutoffs (e.g., `Complete (02:10)`).
   - Risk warning state classes (`normal`, `watch`, `warning`, `critical`) reactively update background and text styling per active BFF risk context.
   - Pending decisions count displays exact counts of un-disposed events.

2. **Honest Unavailable States Verification:**
   - Built-in widget cards do not fabricate operational success or check status statements when telemetry is missing.
   - `ChartNotice` displays truthful telemetry-missing alerts (e.g., `AWAITING TELEMETRY`, `AWAITING DISCLOSURES`) rather than empty placeholder errors or mock chart simulations.

3. **Governed Servant Widget Proposals Verification:**
   - Natural language proposal queries render a controlled `ChartSpec` preview in the workspace grid without modifying the active layout.
   - Adjustment flow supports sub-inputs and adjusts parameters/kinds dynamically.
   - Reject cancels the proposal and dismisses the modal.
   - Plugin Request logs the request and issues a unique registry tracking ID.
   - Accept immediately persists layout revisions to the backend via `patchTradingRoomWorkspaceLayout`, versioning the workspace permanently.

4. **Test Suite Execution:**
   - Focus component test suite (`src/agora/trading-room/WorkspaceGridEditor.test.tsx`) covering the control strip, missing data, data mapping, and servant proposal flows passes fully (4 tests).
   - Entire `TradingRoomPage.test.tsx` passes successfully (62 tests).
