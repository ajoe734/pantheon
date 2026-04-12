# Pantheon Backend Completion Checklist

This checklist is the execution-planning inventory for closing the gap between Pantheon's implemented backend and the BFF/API surface that `front-ai-trading-system` and Lovable need.

## Rebaseline Corrections

- `APP-002-IMPL-BFF` is only partially complete. The current executable BFF still exposes just `GET /health`, `POST /api/v1/operator/commands`, and `GET /api/v1/operator/commands/{command_id}`.
- `APP-002-IMPL-CLI` and `APP-002-IMPL-FE` should be treated as scaffold-complete, not production-complete. The CLI/internal API path and SSE/front-end reconciliation exist as bootstrap scaffolds, but are not yet authoritative enough for real operator use.
- Multi-repo Pantheon <-> `front-ai-trading-system` coordination and Lovable task publishing are already in place on the orchestrator side. The remaining gap is product-grade API completion, not handoff mechanics.

## Completed and Code-Backed Foundations

- Governance and lifecycle foundations are real code: deployment, capital pools, persona-capital bindings, runtime bindings, kill-switch controls, incidents, lineage, telemetry, and evolution all have concrete modules and tests.
- Router, persona, feedback, and related control-plane services exist as executable Pantheon services.
- APP-001 and APP-002 contract layers are complete enough to act as the canonical source of truth for BFF shape, degraded behavior, operator command semantics, and front-end ownership boundaries.
- Pantheon-side coordination for `bff-gap`, `contract-ready`, `lovable-ui-task`, GitHub issue/comment routing, and front-repo mirroring is implemented.

## Current Implementation Truth

### Executable Today

- BFF:
  - `GET /health`
  - `POST /api/v1/operator/commands`
  - `GET /api/v1/operator/commands/{command_id}`
- Internal protected API scaffold:
  - deployment approval
  - runtime pause
  - rollback execution
  - kill-switch activation
  - command status lookup
- Orchestrator / coordination:
  - multi-repo registry
  - coordination file watcher
  - cross-repo issue mapping
  - Lovable task packet publishing
  - front-repo mirroring

### Contracted but Not Implemented for Front-End Use

| Track | Surface IDs | Count | Current State | Needed in Wave 1 |
|---|---|---:|---|---|
| Persona | PS-01 to PS-06 | 6 | Contract only | No |
| Capital pool & binding | CP-01 to CP-04 | 4 | Contract only | CP-02, CP-04 |
| Deployment | DP-01 to DP-04 | 4 | Contract only | DP-02 |
| Runtime | RT-01 to RT-04 | 4 | Contract only | RT-02, RT-04 |
| Telemetry | TL-01 to TL-03 | 3 | Contract only | No |
| Lineage | LN-01 to LN-03 | 3 | Contract only | No |
| Incident | IN-01 to IN-05 | 5 | Contract only | No |
| Evolution | EV-01 to EV-04 | 4 | Contract only | No |
| Composed views | Deployment review, incident response, post-incident review, persona management | 4 | Contract only | Deployment review |
| SSE streams | Runtime events, incidents, kill-switch updates | 3 | No server implementation | No |

### Scaffold-Only Areas That Must Be Hardened

- `POST /api/v1/operator/commands` performs auth/RBAC/validation and returns receipts, but actual execution still runs through a stub async worker.
- Internal API endpoints return placeholder responses and do not yet represent canonical execution truth.
- `pantheon-admin` prints intent/dry-run output rather than driving the protected control path.
- Front-end SSE reconciliation helpers exist only as placeholders and should not be treated as production-ready.

## Locked Planning Decisions

- The first end-to-end proving page is `F-042 Promotion Review`.
- Wave 1 keeps the generic write entrypoint `POST /api/v1/operator/commands`; resource-shaped write routes are deferred until after the first credible UI integration lands.
- SSE is not a Wave 1 blocker.
- Lovable remains a human-triggered UI acceleration lane, not a zero-touch execution dependency.
- Work will be sliced by vertical operator workflow rather than by attempting all 33 read surfaces in a single batch.

## Execution Inventory by Delivery Wave

### Wave 0: Rebaseline and Execution Guardrails

- Reconcile task-board semantics so scaffold-complete work is not mistaken for production completion.
- Freeze the execution target to `F-042 Promotion Review` and record the wave order in planning artifacts.
- Ensure the sibling `front-ai-trading-system` checkout, `.coordination/`, and Lovable handoff paths are present and validated.
- Keep the supervisor in planning mode until the execution packet is approved.

### Wave 1: Promotion Review Vertical Slice

Required surfaces:

- `DP-02`
- `CP-02`
- `CP-04`
- `RT-02`
- `RT-04`
- `GET /api/v1/operator/deployment-review/{plan_id}`

Required write/control work:

- Replace deployment-related command stub execution with real protected execution.
- Shape `allowedActions.canPromoteToPaper`, `latestRun.progress`, and `review.riskSummary` from backend truth.
- Keep `POST /api/v1/operator/commands` as the write route, with front-end SDK wrappers if needed.
- Produce example payloads, fixtures, and handoff docs for Lovable/front-end use.

Wave 1 exit criteria:

- Promotion Review renders with no mocks.
- CTA visibility is backend-driven only.
- Command submit/poll returns authoritative status, not stub success.
- The secondary path (`pantheon-admin` / internal API) can perform the same safe action if the UI is unavailable.

### Wave 2: Incident Response Vertical Slice

Required surfaces:

- `IN-02`
- `RT-03`
- `TL-02`
- `RT-04`
- `EV-04`
- `IN-05`
- `GET /api/v1/operator/incident-response/{incident_id}`

Required write/control work:

- Real pause / rollback / kill-switch execution path.
- Canonical degraded states and operator fallback guidance.
- CLI fallback parity for incident actions.

Wave 2 exit criteria:

- Incident Response page renders from Pantheon only.
- Pause, rollback, and kill-switch actions work through real execution and audit paths.
- Degraded and unavailable states remain explicit and safe.

### Wave 3: Post-Incident Review and Evolution

Required surfaces:

- `IN-04`
- `EV-01`
- `EV-02`
- `EV-03`
- `EV-04`
- `LN-01`
- `TL-03`
- `GET /api/v1/operator/post-incident-review/{incident_id}`

Wave 3 exit criteria:

- Post-incident analysis is page-shaped and uses canonical lineage, telemetry, and evolution data.
- Evolution decisions are reviewable and executable through the same command/control path.

### Wave 4: Persona Management and Remaining Catalog Surfaces

Required surfaces:

- `PS-01` to `PS-06`
- remaining `CP-*`, `DP-*`, `RT-*`, `TL-*`, `LN-*`, `IN-*`, `EV-*` list/detail routes not already delivered in Waves 1 to 3
- `GET /api/v1/operator/persona-management/{persona_id}`

Wave 4 exit criteria:

- Persona lifecycle management no longer depends on local joining or demo providers.
- All contractual list/detail read surfaces have executable implementations.

### Wave 5: SSE, Reconciliation, and Production Front-End Rollout

Required work:

- `GET /api/v1/runtime/{runtime_id}/events/stream`
- `GET /api/v1/incidents/stream`
- `GET /api/v1/kill-switch/updates`
- End-to-end reconnect / replay / stale-state reconciliation
- Front-end BFF base URL cutover, generated types/hooks refresh, and Lovable packet validation against live contracts

Wave 5 exit criteria:

- Runtime/incident/kill-switch live updates are real and reconnect safely.
- Front-end no longer references legacy BFF endpoints.
- Lovable receives prompt packets and mirrored examples that correspond to live Pantheon routes.

## Must-Have Completion Work Before Pantheon Is "Front-End Ready"

- Deliver Wave 1 end-to-end, including real read surfaces, composed view, and authoritative command execution.
- Upgrade internal API from placeholder behavior to canonical protected control behavior.
- Upgrade `pantheon-admin` from scaffold to usable fallback path.
- Complete Incident Response and Post-Incident vertical slices so operator safety workflows are not stranded behind mocks.
- Complete remaining contractual read surfaces and the three SSE transports.

## Risks and Tracking Items

- Gemini planning workers are repeatedly failing with an unexpected provider-side error, so the current packet must be synthesized from submitted readouts plus direct code audit rather than waiting indefinitely.
- Claude remains occupied by an approval-suspended `APP-002-IMPL-BFF` worker; do not let that stale lane block planning convergence.
- Existing `done` task states for scaffold-only work can mislead the dashboard and future slices unless the execution plan explicitly rebaselines them.
