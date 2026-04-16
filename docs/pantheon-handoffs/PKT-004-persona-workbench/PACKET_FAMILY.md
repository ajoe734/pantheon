# PKT-004 Persona Workbench Wave 1 — Canonical Packet Family

## Header

- Packet family ID: `PKT-004`
- Workbench: Persona Workbench
- Phase origin: `BP5-WB-001`
- Lovable readiness: **ready for Wave 1** — all four Wave 1 modules have published BFF contracts, screen specs, example payloads, and Lovable handoff packets; Wave 2 Persona Workbench IA and net-new persona policy panels remain deferred
- Recommended wave: Wave 1 for `PM-01`, `PS-01` to `PS-06`, `CP-01` to `CP-04`, and shared `DP-01` to `DP-04`; Wave 2 for standalone Persona Workbench IA, `Tool profile`, `Consult policy`, and shared runtime or lineage drilldowns
- Owner: Codex2
- Reviewer: Claude

---

## Objective

Package the Persona Workbench Wave 1 surfaces into one truthful packet family so frontend implementation can proceed without inventing persona, binding, capital, deployment, or approval state in the browser. Wave 1 stays read-heavy: the only write-path authority exposed here is the existing operator-command path used by the Persona Management composed screen. Governance approvals and promotions remain in `PKT-001`.

---

## Existing Pantheon Support (pre-conditions)

Before using this packet family, treat the following artifacts as canonical:

| Artifact | Location | What it defines |
|---|---|---|
| Persona Workbench backlog | `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Wave 1 vs Wave 2 scope split for Persona Workbench; shared module boundaries |
| Persona runtime policy | `PERSONA_RUNTIME_MODEL.md` | persona identity, session types, consult metadata, and runtime-facing persona semantics |
| Binding and deployment policy | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | persona-to-capital binding meaning, deployment scope boundaries, and write-owner split |
| `PKT-005` degradation substrate | `docs/bff/PKT-005-degradation-banner.md` | canonical degradation banner inheritance and `meta.surfaces` behavior |
| `PKT-001` governance surfaces | `docs/bff/PKT-001-deployment-review-console.md`, `docs/bff/PKT-001-governance-review-queue.md` | deployment review and governance review remain the write-owning governance surfaces |
| APP-002 Persona and catalog sidecars | prior APP-002 packetization inputs | the composed persona-management view and remaining catalog read routes already exist and are the basis for this Wave 1 packet family |

The important boundary is simple: Persona Workbench Wave 1 may read deployment and approval context, but it must not fork governance write authority or invent persona-specific approval commands.

---

## Module Inventory

| Module ID | Module name | Surface scope | Lovable readiness | Published packet artifacts |
|---|---|---|---|---|
| `PM-01` | Persona Management composed screen | one composed persona surface with bindings, sessions, teaching history, and backend-shaped `allowedActions` | ready | `docs/bff/PKT-004-persona-management.md`, `docs/screens/PKT-004-persona-management.md`, `docs/examples/PKT-004-persona-management.json`, `docs/pantheon-handoffs/PKT-004-persona-management/FRONTEND_CHANGE_SPEC.md` |
| `Module A` | Persona Drilldowns | `PS-01` to `PS-06`: persona catalog, persona detail, session list/detail, teaching history, capability snapshot | ready | `docs/bff/PKT-004-persona-drilldowns.md`, `docs/screens/PKT-004-persona-drilldowns.md`, `docs/examples/PKT-004-persona-drilldowns.json`, `docs/pantheon-handoffs/PKT-004-persona-drilldowns/FRONTEND_CHANGE_SPEC.md` |
| `Module B` | Capital / Binding Drilldowns | `CP-01` to `CP-04`: capital-pool list/detail and binding list/detail | ready | `docs/bff/PKT-004-capital-binding-drilldowns.md`, `docs/screens/PKT-004-capital-binding-drilldowns.md`, `docs/examples/PKT-004-capital-binding-drilldowns.json`, `docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/FRONTEND_CHANGE_SPEC.md` |
| `Module C` | Deployment / Approval Drilldowns | shared `DP-01` to `DP-04`: deployment plan list/detail and approval decision list/detail | ready | `docs/bff/PKT-004-deployment-approval-drilldowns.md`, `docs/screens/PKT-004-deployment-approval-drilldowns.md`, `docs/examples/PKT-004-deployment-approval-drilldowns.json`, `docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns/FRONTEND_CHANGE_SPEC.md` |

---

## Wave 1 Scope

Wave 1 for Persona Workbench includes:

- `PM-01 Persona Management`
- `PS-01` to `PS-06` Persona Drilldowns
- `CP-01` to `CP-04` Capital / Binding Drilldowns
- shared `DP-01` to `DP-04` Deployment / Approval Drilldowns

This Wave 1 family is valid because every included surface already has either:

- a canonical composed BFF route, or
- an approved existing read route that the packet explicitly reuses without introducing a shadow aggregate

No Wave 1 module requires a net-new BFF route before frontend handoff.

---

## Wave 2 Deferred Scope

The following Persona Workbench surfaces are explicitly **not** part of `PKT-004` Wave 1:

- standalone Persona Workbench list/detail IA shell
- `Tool profile`
- `Consult policy`
- shared runtime drilldowns
- shared telemetry / lineage drilldowns
- shared incident / evolution drilldowns

These remain Wave 2 because they still need either persona-specific shell contracts, shared packet families owned elsewhere, or net-new BFF routes before a truthful Lovable handoff is possible.

---

## Backend Dependency Notes

Wave 1 backend dependency truth:

| Scope | Backend status | Truthful note |
|---|---|---|
| `PM-01 Persona Management` | composed route exists | use `GET /api/v1/operator/persona-management/{persona_id}` and `POST /api/v1/operator/commands`; `allowedActions` and degraded-panel behavior are backend-shaped |
| `Module A` Persona Drilldowns | read routes exist | use `/api/v1/personas`, `/api/v1/personas/{persona_id}`, `/api/v1/personas/{persona_id}/sessions`, `/api/v1/sessions/{session_id}`, `/api/v1/personas/{persona_id}/teaching`, `/api/v1/personas/{persona_id}/capabilities`; no client-side joins or client-side filters |
| `Module B` Capital / Binding Drilldowns | read routes exist | use `/api/v1/capital-pools`, `/api/v1/capital-pools/{pool_id}`, `/api/v1/bindings`, `/api/v1/bindings/{binding_id}`; this module stays read-only |
| `Module C` Deployment / Approval Drilldowns | read routes exist | use `/api/v1/deployment-plans`, `/api/v1/deployment-plans/{plan_id}`, `/api/v1/approval-decisions`, `/api/v1/approval-decisions/{decision_id}`; approval and promotion writes stay in `PKT-001` |

Shared caveats that remain non-blocking for Wave 1:

- `PM-01`: `snapshot=preferred` is accepted but not enforced as a true cross-surface alignment guarantee in v1.
- `PM-01`: degradation flags are not tied to `BFF_READ_SURFACE_STATE`; surfaces degrade when a sub-surface returns `None` or empty.
- `Module A`, `PM-01`, and `Module C`: `viewer` tokens are rejected; operator-grade tokens are required.
- `Module C`: all deployment and approval drilldowns are read-only and must link out to `PKT-001` for governance actions.

Known non-blocking backend gaps carried forward into Wave 1:

| Gap ID | Surface | Current truth | Why it does not block Wave 1 |
|---|---|---|---|
| `WB-GAP-01` | `PM-01` / `PS-01` | there is no multi-persona Persona Workbench home composed view; `GET /api/v1/personas` remains a flat browse surface rather than a summary dashboard | Wave 1 uses `PS-01` browse plus drilldowns; the home IA is explicitly deferred to Wave 2 |
| `WB-GAP-02` | `PS-03` | session list pagination is absent in v1; the persona session list returns a single response set | acceptable for current packetization; pagination remains a Wave 2 refinement |
| `WB-GAP-03` | `PS-01` / `PS-02` | persona list/detail still read from the current BFF read store and are not yet extended through the separate canonical snapshot adapter path | current seed/demo-backed read model is sufficient for packetization; adapter convergence remains Wave 2 |
| `WB-GAP-04` | `DP-03` | the approval-decision `time_range` filter is accepted but deferred in v1, so UI must not rely on it for correctness | the read route still exists and is usable for Wave 1 history browsing |
| `WB-GAP-05` | `DP-02` | deployment-plan detail is not the full operator review surface; full command authority still lives in `GET /api/v1/operator/deployment-review/{plan_id}` | this is the intended shared-boundary design; Wave 1 links to `PKT-001` instead of recreating the composed governance screen |

---

## Cross-Cutting Rules

### No shadow data sources

The frontend must use the published BFF routes only. This packet family does not authorize:

- client-side joins across persona, capital, deployment, or approval state
- local derivation of CTA authority
- mock or demo provider fallbacks
- persona-local approval or promotion actions

### Governance boundary

Deployment review, approval decisions, promotion, reject, and similar governance actions remain owned by:

- `PKT-001 Deployment Review Console`
- `PKT-001 Governance Review Queue`

`Module C` may expose read-only context and cross-links into those screens, but it must not duplicate governance commands.

### Degradation inheritance

Where a module exposes `meta.surfaces` or other canonical staleness signals, the frontend must render the inherited degradation banner and degraded-panel placeholders rather than hiding panels or converting degraded state into a false empty state.

---

## Promotion Criteria

`PKT-004` Wave 1 is considered packetized and ready for frontend execution because:

1. Each Wave 1 module has a published BFF contract, screen spec, example payload, and frontend change spec.
2. The packet family reuses existing BFF routes instead of introducing a persona-local shadow backend.
3. Shared governance boundaries are explicit: approval and promotion writes stay with `PKT-001`.
4. Deferred Wave 2 surfaces are named explicitly rather than being implied as part of Wave 1.

---

## Handoff Bundle Map

For frontend execution, use the module-local handoff bundles already published under:

- `docs/pantheon-handoffs/PKT-004-persona-management/`
- `docs/pantheon-handoffs/PKT-004-persona-drilldowns/`
- `docs/pantheon-handoffs/PKT-004-capital-binding-drilldowns/`
- `docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns/`

Each module already has matching `contract-ready` and `lovable-ui-task` artifacts under `.coordination/responses/`.

---

## Summary

`PKT-004` now acts as the canonical Persona Workbench Wave 1 packet family. It covers the composed persona-management screen, persona drilldowns, capital or binding drilldowns, and shared deployment or approval drilldowns, while preserving the existing backend authority split: Persona Workbench reads and operator-command controls stay here; governance writes stay in `PKT-001`; Wave 2 persona policy and shared drilldown surfaces remain deferred until their contracts are real.
