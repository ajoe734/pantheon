# Consensus Packet

## Decision Summary

- Scope: complete the Pantheon backend work required for real `front-ai-trading-system` and Lovable integration, including BFF read surfaces, composed views, authoritative operator command execution, internal fallback path, SSE rollout timing, and handoff packets.
- Accepted architecture:
  - Pantheon owns all `/api/*` read contracts, page-shaped responses, allowed actions, operator command authority, RBAC, and audit semantics.
  - `front-ai-trading-system` owns pages, components, UX states, client wiring, and presentation logic.
  - Lovable remains a human-triggered UI acceleration lane that consumes GitHub-synced code and Pantheon-generated task packets; it is not the zero-touch execution engine.
  - GitHub remains the delivery / handoff / audit bus, not the runtime control plane.
- Delivery order:
  - Wave 0: rebaseline and execution guardrails
  - Wave 1: Promotion Review vertical slice
  - Wave 2: Incident Response vertical slice
  - Wave 3: Post-Incident Review + Evolution
  - Wave 4: Persona Management + remaining contractual read surfaces
  - Wave 5: SSE, reconciliation, and production front-end rollout

## Current Truth Reconfirmed

- The backend foundation is strong: governance, runtime, incident, telemetry, lineage, and evolution modules are code-backed.
- The Pantheon-side Lovable / front-repo collaboration mechanism is built: `bff-gap`, `contract-ready`, `lovable-ui-task`, prompt packets, and front-repo mirroring already exist.
- The BFF is still not front-end ready: only `GET /health`, `POST /api/v1/operator/commands`, and `GET /api/v1/operator/commands/{command_id}` are executable today.
- `APP-002-IMPL-CLI` and `APP-002-IMPL-FE` should be treated as scaffold-complete, not production-complete.

## Agreed Architectural Decisions

- First proving page: `F-042 Promotion Review`
- First-wave write route: keep generic `POST /api/v1/operator/commands`
- Resource-shaped write routes: defer until after Wave 1
- SSE: defer until after Wave 1
- Slicing model: vertical operator workflow slices, not flat endpoint-by-endpoint batch completion

## Agreed Task Slices

| Task | Wave | Owner | Reviewer | Scope | Depends On | Acceptance |
|---|---|---|---|---|---|---|
| `APP-002-W0-REBASELINE` | 0 | Codex | Claude | Rebaseline task-board truth, align planning packet, and record scaffold-vs-production corrections for APP-002 backend work. | None | Checklist, starter draft, review round, and consensus packet align on actual implementation truth; dashboard/planning state reflects the new packet. |
| `APP-002-W1-READ-DEPLOYMENT` | 1 | Codex | Qwen | Implement `DP-02`, `CP-02`, `CP-04`, `RT-02`, `RT-04`, and `GET /api/v1/operator/deployment-review/{plan_id}` as a page-shaped Promotion Review response. | `APP-002-W0-REBASELINE` | F-042 renders from Pantheon only; example payload and degraded metadata are present; no mock-only fields remain. |
| `APP-002-W1-COMMAND-DEPLOYMENT` | 1 | Claude | Gemini | Replace deployment-related stub command execution with authoritative protected execution and truthful command status for Promotion Review actions. | `APP-002-W1-READ-DEPLOYMENT` | Promotion commands no longer report stub success; command receipt, polling, audit trail, and failure modes reflect real governance/runtime outcomes. |
| `APP-002-W1-FRONT-HANDOFF` | 1 | Copilot | Codex | Publish `contract-ready` artifacts, example payloads, generated types/hooks guidance, and Lovable prompt packets for F-042. | `APP-002-W1-READ-DEPLOYMENT` | Pantheon emits mirrored handoff docs and task packets that the front repo and Lovable can consume without inventing client-side joins. |
| `APP-002-W2-READ-INCIDENT` | 2 | Qwen | Claude | Implement `IN-02`, `RT-03`, `TL-02`, `RT-04`, `EV-04`, `IN-05`, and `GET /api/v1/operator/incident-response/{incident_id}`. | Wave 1 complete | Incident Response page renders from Pantheon only with explicit degraded/unavailable states. |
| `APP-002-W2-CONTROL-INCIDENT` | 2 | Codex | Gemini | Harden pause / rollback / kill-switch execution and status reporting for incident workflows. | `APP-002-W2-READ-INCIDENT` | Incident actions execute through canonical control paths and remain safe under degraded conditions. |
| `APP-002-W2-CLI-FALLBACK` | 2 | Copilot | Claude | Turn `pantheon-admin` and the internal API from scaffold into a usable secondary control path for deployment and incident actions. | `APP-002-W1-COMMAND-DEPLOYMENT`, `APP-002-W2-CONTROL-INCIDENT` | CLI can perform approved operator actions against the hardened internal API; docs and smoke tests reflect real behavior. |
| `APP-002-W3-POSTINCIDENT-EVOLUTION` | 3 | Qwen | Codex | Implement `IN-04`, `EV-01`, `EV-02`, `EV-03`, `EV-04`, `LN-01`, `TL-03`, and `GET /api/v1/operator/post-incident-review/{incident_id}`. | Wave 2 complete | Post-incident and evolution review views are executable and page-shaped. |
| `APP-002-W4-PERSONA-MGMT` | 4 | Gemini | Claude | Implement `PS-02`, `PS-03`, `PS-05`, `CP-03`, `CP-04`, and `GET /api/v1/operator/persona-management/{persona_id}`. | Wave 3 complete | Persona Management renders without local joins or demo providers. |
| `APP-002-W4-REMAINING-CATALOG` | 4 | Codex | Copilot | Finish remaining contractual list/detail read surfaces not already delivered in Waves 1 to 3. | Wave 4 vertical slices underway | All contractual read surfaces in `BFF_API_CONTRACT.md` have executable implementations and docs/examples. |
| `APP-002-W5-SSE-LIVE` | 5 | Gemini | Codex | Implement runtime, incident, and kill-switch SSE transports plus reconnect/replay semantics and front-end reconciliation hooks. | Waves 2 to 4 complete | All three SSE streams are real, reconnect safely, and integrate with front-end state transitions. |
| `APP-002-W5-LOVABLE-CUTOVER` | 5 | Copilot | Qwen | Cut the front repo to Pantheon BFF defaults, validate generated handoff packets against live contracts, and verify Lovable prompt packets against the production flow. | Waves 1 to 5 complete | `front-ai-trading-system` no longer points at legacy BFF URLs; Lovable handoffs match live Pantheon behavior. |

## Execution Notes

- If Gemini remains unhealthy as a provider, reassign Gemini-owned slices to Codex/Copilot rather than stalling the execution waves.
- If Claude remains occupied by the stale approval-suspended `APP-002-IMPL-BFF` worker, park that lane before materializing Wave 1 execution tasks.
- The sidecar/handoff lane should trail each vertical slice rather than waiting until the end of the program.
- Every vertical slice must ship with:
  - executable route(s)
  - example payload(s)
  - degraded-state behavior
  - contract docs
  - tests
  - front-handoff artifacts
  - CLI/internal fallback coverage where the slice includes write actions

## Open Questions / Human Gate

- No architecture-level blocker remains. The remaining human gate is operational:
  - approve this packet as the source of truth for task materialization
  - decide whether to park the stale `APP-002-IMPL-BFF` approval-suspended Claude lane immediately or after Wave 1 task creation
- Tracking items, not blockers:
  - Gemini planning worker instability
  - dashboard/task-board semantics that still label scaffold work as `done`

## Acceptance Note

- This packet is execution-ready and grounded in direct codebase verification plus the submitted Codex/Qwen readouts.
- Planning status should move to `ready_for_human`; once accepted, the proposed slices can be materialized into the execution board.
