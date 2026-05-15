# PKT-014 Operator Paper / Live Drift — Lovable Change Feedback

Reviewed the `operator-paper-live-drift` implementation in
`ajoe734/front-ai-trading-system` against the PKT-014 BFF contract, screen
spec, example payload, and the current Pantheon BFF workspace.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Paper / Live Drift screen is wired to the published PKT-014 read route
through `operatorApi.getPaperLiveDrift()` and stays aligned with the
single-route, backend-owned comparison model. The UI preserves backend-owned
drift-group ordering, threshold evaluation, evidence refs, recommended
actions, and explicit unavailable handling without deriving drift logic in the
browser.

## Verified Against Pantheon

- `GET /api/v1/operator/paper-live-drift/{runtime_id}` is consumed through the
  shared BFF client only. No component-level raw `fetch` or `axios` path was
  introduced.
- The reviewed screen validates all required PKT-014 top-level fields plus the
  full required `meta.surfaces.*` set before rendering. Missing contract fields
  produce an explicit `bff-gap` alert state instead of local drift synthesis.
- The comparison header renders `runtime_id`, `plan_ref`, `artifact_ref`, and
  the paper/live stage boundary from the payload.
- The threshold summary renders `threshold_evaluation.overall_status`,
  `summary`, and `breached_metric_ids[]` exactly as supplied.
- The drift-group stack renders `drift_groups[]` and nested `metrics[]` in the
  backend-owned order; no client-side sorting or threshold derivation is added.
- `recommended_actions[]` and `evidence_refs[]` are rendered verbatim from the
  payload. The UI does not infer fallback owner actions from raw metric values.
- `meta.surfaces.paper_live_drift = unavailable` renders the explicit
  unavailable treatment and suppresses comparison math instead of rebuilding
  drift state from approval, incident, or telemetry primitives.
- Supporting degraded or unavailable surfaces feed the shared global
  degradation banner through `meta.surfaces`; the banner is not composed from
  browser-owned heuristics.

## Pantheon Validation

- Front static verification passed for the reviewed PKT-014 file slice:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorPaperLiveDrift.tsx src/pages/operator/types.ts`
  - `npm run build`
- Targeted Pantheon contract verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt014_paper_live_drift_contract.py -q`
  - Result: `2 passed`
- Direct local FastAPI `TestClient` probing of the current Pantheon workspace
  returned `200 OK` for `GET /api/v1/operator/paper-live-drift/runtime-042`
  with:
  - `meta.surfaces.paper_live_drift.status = ok`
  - `meta.surfaces.drift_report.status = ok`
  - `threshold_evaluation.overall_status = breached`
  - backend-supplied API-resource refs in `plan_ref`, `evidence_refs[]`, and
    `recommended_actions[].target_ref`
- Local BFF route probing using the same app with controlled store fixtures
  also verified the required non-happy-path branches:
  - degraded case: `200 OK`, `paper_live_drift = degraded`,
    `drift_report = degraded`, baseline and observed snapshots present
  - unavailable case: `200 OK`, `paper_live_drift = unavailable`,
    `drift_report = unavailable`, `paper_baseline = null`,
    `observed_state = null`

## Integration Boundary Notes

- The current sibling front checkout contains the PKT-014 screen and feedback
  bundle only in the working tree. The advertised `ui-done` payload still uses
  `source_commit: HEAD`, so the reviewed publication set is not replay-clean.
- Front `HEAD` (`37a622bca69a95e2aae46aa8c6b0432ad72082a8`) contains
  `src/App.tsx`, `src/lib/bffClient.ts`, and `src/pages/operator/types.ts`, but
  does not contain:
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-014-paper-live-drift/*`
- Pantheon currently emits API-resource hrefs where the PKT-014 screen packet
  describes existing owner-screen navigation through `recommended_actions[]`
  and `plan_ref`. The reviewed UI is correct to render these refs verbatim, but
  Pantheon still needs to clarify whether PKT-014 is promising browser routes
  or API-resource links.

## Pantheon Follow-up

- No new endpoint is authorized in this cycle.
- No browser-side shadow state is authorized in this cycle.
- Pantheon should publish browser-ready owner-screen refs for PKT-014 or revise
  the PKT-014 packet, screen spec, and example payload so they truthfully
  describe API-resource links.
- The front repo must publish the canonical PKT-014 `frontend-feedback`
  request and republish `ui-done` from one truthful immutable front commit that
  actually contains the reviewed screen and feedback bundle.
