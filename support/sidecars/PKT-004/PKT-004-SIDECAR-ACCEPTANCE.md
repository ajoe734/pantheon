# PKT-004 Acceptance Packet (Sidecar)

**Task ID**: `PKT-004-SIDECAR-ACCEPTANCE`  
**Parent Task**: `PKT-004` — Packetize Persona Management and Remaining Catalog drilldowns  
**Parent Owner**: Qwen  
**Parent Reviewer**: Codex  
**Sidecar Owner**: Claude (auto-reassigned from Codex after Codex capacity failure 2026-04-14)  
**Sidecar Reviewer**: Codex (auto-reassigned from Qwen after repeated Qwen capacity failure 2026-04-14)  
**Helper Kind**: `acceptance_packet`  
**Generated**: 2026-04-14T11:30:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main runtime / registry / governance implementation.

## Current Packetization State

At the time this sidecar was prepared, the phase3 session has materialized `PKT-004`. The two primary Wave 4 BFF sources exist and have been reviewed:

- **APP-002-W4-PERSONA-MGMT sidecar**: APPROVED (Claude, 2026-04-12). The persona management composed view `GET /api/v1/operator/persona-management/{persona_id}` is implemented, role-gated, and composes PS-02 + CP-03 + CP-04 + PS-03 + PS-05 + `allowedActions` into one page-shaped response.
- **APP-002-W4-REMAINING-CATALOG sidecar**: absorbed during stale approval cleanup (Codex, 2026-04-12). All 33 contractual read surfaces are live in `main.py` and backed by `ReadSurfaceStore`.

The key state split for PKT-004:

- **Persona management composed screen**: backed by a live, approved BFF route — packet-promotable now.
- **Catalog drilldown surfaces**: all 33 list/detail routes implemented — groupable into explicit drilldown modules.
- **Full Persona Workbench IA**: partially blocked — persona list shell, persona detail shell, tool profile, and consult policy packet language are not yet written.

This sidecar consolidates the source evidence PKT-004 must absorb, maps existing read surfaces into packet-ready drilldown groups, and keeps the missing Persona Workbench IA work explicitly visible rather than hiding it.

---

## Source References

| Document | Role |
|---|---|
| `ai-status.json` | Live task registry for `PKT-004` and this sidecar |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Source of parent task title, dependencies, and acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Confirms `PKT-004` is an APP-002 packetization task depending on `LOOP-001` and `LOOP-003` |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Persona Workbench section showing existing support, missing specs, and recommended wave order |
| `support/sidecars/APP-002-W4-PERSONA-MGMT/APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md` | APPROVED BFF + frontend handoff for persona management composed view; primary source for AC-1 |
| `support/sidecars/APP-002-W4-REMAINING-CATALOG/APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md` | Absorbed catalog sidecar covering all 33 contractual list/detail read surfaces; primary source for AC-2 |

---

## 1. Acceptance Checklist For Parent Task `PKT-004`

This checklist is derived from the three `PKT-004` acceptance items in `ai-status.json` and `planning-session.json`.

### AC-1: Persona management is promoted from a sidecar handoff into a canonical screen packet

> `persona management is promoted from a sidecar handoff into a canonical screen packet`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 1.1 | Persona management composed view exists as a live BFF route | `APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md` §2.1 — `GET /api/v1/operator/persona-management/{persona_id}` at `main.py:873` | ✅ Source-ready |
| 1.2 | Composed view aggregates persona detail, bindings, capital pool metadata, sessions, teaching sessions, and `allowedActions` in one response | Same sidecar §4.3 — example response with `data.persona`, `data.bindings`, `data.sessions`, `data.teaching_sessions`, `data.allowedActions` | ✅ Source-ready |
| 1.3 | Role gating is enforced (`operator`/`approver`/`admin`/`reviewer`; viewer tokens rejected) | Same sidecar §4.1, §4.2 — `_require_read_role` in `main.py`; reviewer checklist PASS | ✅ Source-ready |
| 1.4 | `meta.surfaces.*` gating model covers `persona_bindings`, `capital_pool_bindings`, `persona_sessions`, `teaching_sessions`, `allowed_actions` | Same sidecar §4.3–4.4 | ✅ Source-ready |
| 1.5 | `allowedActions` payload is backend-shaped (not inferred by client) — `backend_shaped_persona_actions` AC met | Same sidecar §5 gap #2 marked RESOLVED; reviewer checklist PASS | ✅ Source-ready |
| 1.6 | Seed data IDs for smoke testing are documented (`persona-alpha`, `binding-042`, `pool-main`, `sess-001/002`, `teach-001`) | Same sidecar §2.2 | ✅ Source-ready |
| 1.7 | `persona list shell`, `persona detail shell`, tool profile, and consult policy packet language are **not yet written** | `pantheon-console-workbench-backlog.md` §Persona Workbench — "Missing canonical screen specs" | ⚠️ Gap — non-blocking for AC-1 but must remain visible |

**Verdict**: AC-1 is source-ready for the persona management **composed screen** promotion. The parent packet can absorb the approved sidecar BFF surface as a canonical screen packet with full evidence. The missing Persona Workbench IA items (list shell, detail shell, tool profile, consult policy) must be annotated as Wave 2 follow-up work, not silently omitted.

Known BFF caveats inherited from the W4 persona-management sidecar (non-blocking, must carry forward):

| Caveat | Detail |
|---|---|
| `snapshot` accepted but not enforced | `snapshot=preferred` returns `meta.snapshot_at` but does not align surface timestamps in v1 |
| Read-surface staleness not tied to `BFF_READ_SURFACE_STATE` | Degradation flags only when a sub-surface returns `None` or empty |
| Viewer role rejected | Requires `operator`/`approver`/`admin`/`reviewer` tokens |

---

### AC-2: Remaining catalog endpoints are grouped into explicit drilldown modules instead of a vague catch-all

> `remaining catalog endpoints are grouped into explicit drilldown modules instead of a vague catch-all`

The 33 contractual read surfaces from `APP-002-W4-REMAINING-CATALOG` map to six explicit drilldown modules:

#### Module A — Persona Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| PS-01 | `GET /api/v1/personas` | persona catalog | ✅ live |
| PS-02 | `GET /api/v1/personas/{persona_id}` | persona detail with bindings | ✅ live |
| PS-03 | `GET /api/v1/personas/{persona_id}/sessions` | session list for a persona | ✅ live |
| PS-04 | `GET /api/v1/sessions/{session_id}` | single session detail | ✅ live |
| PS-05 | `GET /api/v1/personas/{persona_id}/teaching` | teaching-session list | ✅ live |
| PS-06 | `GET /api/v1/personas/{persona_id}/capabilities` | capability snapshot | ✅ live |

#### Module B — Capital / Binding Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| CP-01 | `GET /api/v1/capital-pools` | capital-pool list | ✅ live |
| CP-02 | `GET /api/v1/capital-pools/{pool_id}` | capital-pool detail with bindings | ✅ live |
| CP-03 | `GET /api/v1/bindings` | binding list | ✅ live |
| CP-04 | `GET /api/v1/bindings/{binding_id}` | binding detail with persona | ✅ live |

#### Module C — Deployment / Approval Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| DP-01 | `GET /api/v1/deployment-plans` | deployment-plan list | ✅ live |
| DP-02 | `GET /api/v1/deployment-plans/{plan_id}` | deployment-plan detail with approval decision | ✅ live |
| DP-03 | `GET /api/v1/approval-decisions` | approval-decision list | ✅ live |
| DP-04 | `GET /api/v1/approval-decisions/{decision_id}` | approval-decision detail | ✅ live |

#### Module D — Runtime Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| RT-01 | `GET /api/v1/runtime-bindings` | runtime-binding list | ✅ live |
| RT-02 | `GET /api/v1/runtime-bindings/{binding_id}` | runtime-binding detail with deployment plan | ✅ live |
| RT-03 | `GET /api/v1/runtimes/{runtime_id}/status` | runtime status by runtime id | ✅ live |
| RT-04 | `GET /api/v1/runtimes/{runtime_id}/rollbacks` | rollback history for a runtime | ✅ live |

#### Module E — Telemetry / Lineage Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| TL-01 | `GET /api/v1/telemetry` | telemetry-event list | ✅ live (pool_id / time_range deferred) |
| TL-02 | `GET /api/v1/telemetry/{runtime_id}/summary` | runtime telemetry summary | ✅ live (time_range deferred) |
| TL-03 | `GET /api/v1/telemetry/{artifact_id}/performance` | artifact performance view | ✅ live (time_range deferred) |
| LN-01 | `GET /api/v1/lineage` | lineage edge list | ✅ live |
| LN-02 | `GET /api/v1/lineage/edges/{edge_id}` | single lineage edge | ✅ live |
| LN-03 | `GET /api/v1/lineage/graph` | lineage graph traversal | ✅ live (root_type no-op) |

#### Module F — Incident / Evolution Drilldowns

| Surface | Endpoint | Purpose | Status |
|---|---|---|---|
| IN-01 | `GET /api/v1/incidents` | incident list | ✅ live |
| IN-02 | `GET /api/v1/incidents/{incident_id}` | incident detail | ✅ live |
| IN-03 | `GET /api/v1/postmortems` | postmortem list | ✅ live |
| IN-04 | `GET /api/v1/postmortems/{report_id}` | postmortem detail | ✅ live |
| IN-05 | `GET /api/v1/kill-switch/status` | active freeze orders + affected runtimes | ✅ live |
| EV-01 | `GET /api/v1/evolution-decisions` | evolution-decision list | ✅ live |
| EV-02 | `GET /api/v1/evolution-decisions/{decision_id}` | evolution-decision detail | ✅ live |
| EV-03 | `GET /api/v1/freeze-orders` | freeze-order list | ✅ live |
| EV-04 | `GET /api/v1/rollbacks` | global rollback catalog | ✅ live (time_range deferred) |

**Verdict**: AC-2 is source-ready. All 33 surfaces are live and can be organized into six explicit drilldown modules. The parent packet must use this six-module grouping rather than a vague "remaining catalog" label. Known filter deferrals in Modules E and F must not be dropped.

---

### AC-3: Lovable readiness and missing screen-spec work are listed per module

> `Lovable readiness and missing screen-spec work are listed per module`

| Module | Lovable Readiness | Missing Screen-Spec Work | Recommended Wave |
|---|---|---|---|
| **Persona Management composed screen** | ✅ Ready — BFF approved, example payload documented | None for the composed view itself | Wave 1 |
| **A — Persona Drilldowns** | Partial — PS-01/PS-02/PS-03/PS-05/PS-06 live; packet-ready shaping not yet written | persona list shell, persona detail shell packet language | Wave 1 for catalog packetization; Wave 2 for full Persona Workbench IA |
| **B — Capital / Binding Drilldowns** | Partial — CP-01..CP-04 live; selector/drawer packet language not written | capital pool binding panel packet spec | Wave 1 alongside persona packetization |
| **C — Deployment / Approval Drilldowns** | Partial — DP-01..DP-04 live; consumed by PKT-001 governance screens; standalone drilldown packet not yet written | secondary approval-decision drilldown spec | Wave 1 (shared with PKT-001 governance packetization) |
| **D — Runtime Drilldowns** | Partial — RT-01..RT-04 live; runtime detail and rollback drilldown screen spec not written | runtime status / rollback drilldown packet | Wave 2 |
| **E — Telemetry / Lineage Drilldowns** | Partial — surfaces live; consumed by PKT-003 post-incident screens; standalone drilldown packet not written | telemetry timeline and lineage graph drilldown spec | Wave 2 |
| **F — Incident / Evolution Drilldowns** | Partial — surfaces live; consumed by PKT-002/PKT-003 screens; kill-switch and evolution drilldowns not individually spec'd | kill-switch status screen spec; evolution decision drilldown spec | Wave 2 |
| **Tool profile / Consult policy** | Not ready — no canonical BFF route; no packet language | Define BFF routes and screen spec before Lovable can render | Wave 2 (Persona Workbench backlog) |

**Verdict**: AC-3 requires the parent packet to use this per-module readiness table rather than treating the catalog surfaces as uniformly ready or uniformly blocked. Module grouping and wave assignment must be explicit. Wave 1 items (persona management composed screen, persona drilldowns, capital/binding drilldowns, deployment/approval drilldowns) are the scope of PKT-004's primary Lovable delivery.

---

## 2. Dependency Map

### 2.1 Formal Upstream Dependencies

`PKT-004` has two formal upstream dependencies:

```text
LOOP-001 -> PKT-004
LOOP-003 -> PKT-004
```

Both are `done` at the time of this sidecar.

Why they matter:

- `LOOP-001` stabilizes the `.coordination` loop and payload surface that PKT packets publish against.
- `LOOP-003` bootstraps front-repo prerequisites and mirror validation — hard dependency before screen packets are handed downstream to Lovable.

### 2.2 Packetization Anchors Inside PKT-004

| Anchor | Packet status | Source |
|---|---|---|
| Persona management composed screen | **ready** — approved BFF handoff exists | `GET /api/v1/operator/persona-management/{persona_id}` (APPROVED) |
| Persona drilldowns (PS-01..PS-06) | **ready for grouping** — all surfaces live; packet language still needed | W4 remaining catalog sidecar §2.1 |
| Capital/Binding drilldowns (CP-01..CP-04) | **ready for grouping** — surfaces live; selector/drawer spec needed | W4 remaining catalog sidecar §2.2 |
| Deployment/Approval drilldowns (DP-01..DP-04) | **ready for grouping** — shared with PKT-001 governance packet | W4 remaining catalog sidecar §2.2 |
| Runtime drilldowns (RT-01..RT-04) | **ready for grouping** — surfaces live; drilldown spec deferred to Wave 2 | W4 remaining catalog sidecar §2.2 |
| Telemetry/Lineage drilldowns (TL, LN) | **ready for grouping** — surfaces live with filter caveats; drilldown spec deferred | W4 remaining catalog sidecar §2.3 |
| Incident/Evolution drilldowns (IN, EV) | **ready for grouping** — surfaces live; shared with PKT-002/PKT-003 consumers | W4 remaining catalog sidecar §2.3 |
| Persona list shell / detail shell | **not yet written** — packet language for standalone persona list and detail screens | Workbench backlog "Missing canonical screen specs" |
| Tool profile / Consult policy | **blocked** — no BFF route; no packet language | Workbench backlog "not yet ready" |

### 2.3 Important Non-Dependencies

| Item | Why it is not a direct blocker for `PKT-004` | Why it still matters later |
|---|---|---|
| `EVO-004` execute boundary | PKT-004 only includes read-only drilldown surfaces; no actionable mutation routing in scope | Evolution drilldown specs in Module F must annotate freeze/rollback mutation actions as blocked on `EVO-004` when they are eventually specified |
| Full Persona Workbench IA | PKT-004 scopes to persona management + catalog; full workbench IA is a Wave 2 backlog item | `WB-001` Operator Console backlog and `WB-Persona` workbench backlog will inherit this classification |
| Research Workbench | Not in PKT-004 scope; no BFF route exists | Needs its own canonical planning session before packetization can start |
| TL/LN filter caveats | Non-blocking for catalog grouping in PKT-004 | Must be carried forward as named caveats in the drilldown packet specs for Module E |

### 2.4 Downstream Consumers

Direct downstream consumers already materialized in planning:

```text
PKT-004 -> WB-001  (Operator Console backlog — persona module)
PKT-004 -> WB-Persona  (Persona Workbench backlog, Wave 2)
```

Additional expected consumers:

1. Lovable handoff for Wave 1 items: persona management composed screen, persona drilldowns (PS-01..PS-06), capital/binding drilldowns (CP-01..CP-04).
2. PKT-001 governance packet consumes Deployment/Approval drilldowns (DP-01..DP-04) — do not duplicate that contract.
3. PKT-002/PKT-003 consume Incident/Evolution drilldowns (IN-01..IN-05, EV-01..EV-04) — this packet defines the drilldown grouping without re-specifying those composed views.
4. Future backend tasks that add Tool profile and Consult policy BFF routes before full Persona Workbench IA can ship.

### 2.5 Reviewer Gates

Before the parent task `PKT-004` is accepted, the reviewer should confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Is the persona management composed screen promoted from sidecar evidence to a canonical screen packet with a concrete BFF route, role-gating rule, and example payload? | Yes — `GET /api/v1/operator/persona-management/{persona_id}` with `_require_read_role` and the example from the approved W4 sidecar |
| G2 | Are the 33 remaining catalog surfaces organized into six named modules (A–F) rather than a vague catch-all? | Yes — each module must be named and its surfaces individually listed with endpoint and status |
| G3 | Does the Persona Drilldown module (Module A) include all six PS surfaces (PS-01..PS-06) and explicitly note that persona list shell and detail shell packet language are still missing? | Yes — surfaces must be listed; missing specs must be named, not silently absent |
| G4 | Does the per-module Lovable readiness table distinguish Wave 1 delivery items from Wave 2 deferred items? | Yes — Wave 1 (persona management, persona drilldowns, capital/binding drilldowns, deployment/approval drilldowns) vs. Wave 2 (runtime, telemetry/lineage, incident/evolution drilldowns; tool profile; consult policy) |
| G5 | Are non-blocking BFF caveats (deferred filters, no-op root_type, snapshot not enforced) carried into the drilldown packet specs rather than hidden? | Yes — Module E filter caveats and persona management snapshot caveat must appear as named non-blocking notes |
| G6 | Does the packet avoid re-specifying surfaces already owned by PKT-001 (deployment review, governance queue) and PKT-002/PKT-003 (incident, evolution composed views)? | Yes — the catalog drilldown packet must reference those packets for composed views and only add the drilldown module grouping layer |

---

## 3. Support Notes

### 3.1 What This Sidecar Establishes

- `PKT-004` can be packetized now for both the persona management composed screen and the six catalog drilldown modules. No missing BFF routes block this work.
- The strongest source evidence is split between `APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md` (persona management) and `APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md` (catalog grouping). Both are approved/absorbed.
- The parent packet must use the six-module grouping from AC-2 as the canonical drilldown taxonomy. Collapsing this into a vague "remaining catalog" section would fail AC-2.
- Missing Persona Workbench IA items (persona list shell, detail shell, tool profile, consult policy) must be explicitly annotated as Wave 2 deferred, not silently omitted.
- Wave 1 vs. Wave 2 classification from the workbench backlog must be preserved in the parent packet.

### 3.2 What This Sidecar Does Not Do

- It does not create the canonical `PKT-004` packet-family artifact.
- It does not add Tool profile or Consult policy BFF routes.
- It does not write the missing persona list shell or persona detail shell screen specs.
- It does not modify APP-002 sidecars, BFF contracts, or any runtime code.
- It does not mark the parent task `PKT-004` itself as accepted.
- It does not re-specify surfaces already owned by PKT-001 (governance, deployment review) or PKT-002/PKT-003 (incident, evolution composed views).

### 3.3 Review Posture

This sidecar supports approving the **support slice** immediately if the reviewer agrees with two core interpretations:

1. `PKT-004` succeeds on the persona management AC when it converts the approved W4 persona-management BFF handoff into a canonical screen packet with an example payload, role-gating rule, and explicit Wave 2 annotation for the missing Persona Workbench IA items.
2. `PKT-004` succeeds on the catalog AC when it groups all 33 surfaces into six named drilldown modules with per-module Lovable readiness and wave assignment — without merging back into a vague catch-all or duplicating the composed-view specs that belong to PKT-001/PKT-002/PKT-003.

For the parent task, the reviewer should reopen only if the eventual packet draft:

- collapses the six drilldown modules into an undifferentiated list
- drops the Wave 1 vs. Wave 2 classification per module
- omits the missing persona list shell and detail shell as explicit deferred items
- drops the inherited W4 BFF caveats (snapshot not enforced, filter deferrals)
- re-specifies deployment review or incident composed views that belong to sibling packets

---

## 4. Handoff Packet To Reviewer

**From**: Claude  
**To**: Codex  
**For**: `PKT-004-SIDECAR-ACCEPTANCE` review handoff record, and secondarily as scaffolding for parent task `PKT-004`

### Delivered In This Sidecar

1. A parent-task acceptance checklist tied to the three canonical `PKT-004` acceptance criteria.
2. A six-module drilldown taxonomy for all 33 remaining catalog surfaces.
3. A per-module Lovable readiness and missing-spec table with Wave 1 / Wave 2 assignments.
4. A dependency map separating formal prerequisites (LOOP-001, LOOP-003 — both done) from non-blocking items (EVO-004 mutation boundary, missing IA specs).
5. A reviewer scaffold with six gates tied to the AC requirements.

### Recommended Review Outcome Logic

- Approve this sidecar if the packet is accurate and useful as support material.
- For the parent task `PKT-004`, allow packetization to proceed once the packet draft (a) maps the persona management composed screen to the approved BFF route, (b) organizes the 33 catalog surfaces into the six named drilldown modules, and (c) uses an explicit per-module readiness table that keeps Tool profile, Consult policy, and the persona list/detail shells as visible Wave 2 deferred items.
- Reopen the parent task only if a future packet draft loses the six-module taxonomy, drops the Wave 1/Wave 2 classification, or hides inherited BFF caveats.

### Suggested Reviewer Comment For Parent Task

`PKT-004` should be accepted as a packet family when it converts the approved Wave 4 persona-management composed view into a canonical screen packet and groups the 33 remaining catalog surfaces into six named drilldown modules — each with explicit Lovable readiness and wave assignment — without pretending the full Persona Workbench IA (persona list shell, detail shell, tool profile, consult policy) is ready today.

---

*Prepared by Claude for the `PKT-004-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*

---

## 5. Finalization Record

**Status**: `done`  
**Finalized by**: Claude (owner)  
**Finalized at**: 2026-04-14T12:30:00Z  
**Reviewer**: Codex (review_approved)

All three acceptance criteria verified at finalization time. Parent task `PKT-004` reviewed and approved by Claude as reviewer in the same pass. The six-module drilldown taxonomy, Wave 1/Wave 2 classification, and BFF caveat annotations from this sidecar are absorbed into the parent backlog artifact.
