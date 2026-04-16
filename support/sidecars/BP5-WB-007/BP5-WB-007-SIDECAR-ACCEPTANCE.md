# BP5-WB-007 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-WB-007-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-WB-007` — Packetize the Trainer Workbench family
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex`
**Date:** `2026-04-16`
**Status:** `done`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, BFF routes, or governance truth. It records the formal acceptance criteria,
> dependency map, and module-ordering checklist for BP5-WB-007 so the assigned reviewer can judge
> the parent slice quickly and the parent owner has a compact acceptance scaffold when the packet
> family is delivered.
>
> Current repo state: `BP5-WB-007` is listed as `todo` under Claude ownership, with Codex as the
> assigned reviewer after the latest automated reassignment. Both upstream dependencies are `done`:
> - `BP5-SVC-014` (persona platform and consultation read surfaces)
> - `BP5-SVC-009` (telemetry ingest service and shock-absorption path)
>
> The Trainer Workbench backlog is established in
> `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
> as Wave 3, covering four modules: Teaching dialog → Parameter controls → Before/after compare →
> Teaching replay. Currently no dedicated Trainer BFF mutation route or standalone Trainer packet
> family exists. The only live support is read-only teaching-session history exposed inside Persona
> Management surfaces (`GET /api/v1/operator/persona-management/{persona_id}` and
> `GET /api/v1/personas/{persona_id}/teaching`), which are Persona drilldowns rather than a
> Trainer-owned workflow.

---

## 1. Purpose

This packet gives `Codex` a compact review surface for `BP5-WB-007-SIDECAR-ACCEPTANCE` and serves
as a ready-made acceptance scaffold for the current parent-task delivery of `BP5-WB-007`:

1. A criterion-by-criterion acceptance checklist for the Trainer Workbench packet family
2. A concrete file / artifact inventory the parent reviewer should confirm once the parent task delivers the packet family
3. A dependency map showing what this task unblocks and how it relates to the BFF and service layer
4. Module ordering rules from the backlog that the packet plan must preserve

The central structural requirement: **BP5-WB-007 must deliver real canonical packets with explicit
BFF route prerequisites for all four Trainer Workbench modules. No Trainer screen may be handed to
Lovable before its session-mutation, compare, and replay contracts stop being L3 design intent and
become canonical BFF-facing truth. Teaching-history read surfaces inside Persona Management are
evidence inputs only; they do not replace a Trainer-owned packet family.**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the phase-5 planning session (sourced from `ai-status.json`):

- **AC-1:** `Trainer Workbench surfaces have canonical packet language and explicit BFF prerequisites`
- **AC-2:** `teaching-history and trainer-specific flows are separated instead of hidden inside Persona screens`

### AC-1: Trainer Workbench surfaces have canonical packet language and explicit BFF prerequisites

| Check | Evidence needed | Status |
|---|---|---|
| Teaching dialog has a canonical packet entry | reviewer should confirm a packet record or handoff file exists covering session shell, transcript layout, event composer, session status copy, and actor context display | PENDING |
| Teaching dialog lists its BFF prerequisites explicitly | `POST /api/v1/trainer/sessions`, `GET /api/v1/trainer/sessions/:id`, and `POST /api/v1/trainer/sessions/:id/message` must be named as required routes, plus the trainer-session lifecycle contract and `session_type=trainer` identity constraint | PENDING |
| Parameter controls has a canonical packet entry | reviewer should confirm a packet record exists covering editable control-state fields, patch confirmation affordance, and validation or warning states | PENDING |
| Parameter controls lists its BFF prerequisites and dependency on Teaching dialog session context | `POST /api/v1/trainer/sessions/:id/patch`, the control-state schema, and the allowed-range and validation contract must be named; the packet must call out that this module depends on Teaching dialog establishing an active session context | PENDING |
| Before/after compare has a canonical packet entry | reviewer should confirm a packet record exists covering metric panels, control-state diff layout, warning hierarchy, and degraded `preview_unavailable` copy | PENDING |
| Before/after compare lists its BFF prerequisites and dependency on Parameter controls | the preview or rapid-eval route, the before/after compare payload contract, and degraded-state semantics must be named; the packet must call out that this module depends on Parameter controls producing a patchable candidate state | PENDING |
| Teaching replay has a canonical packet entry | reviewer should confirm a packet record exists covering history shell, ordered event timeline, evidence drawer, and commit/discard/replay action copy | PENDING |
| Teaching replay lists its BFF prerequisites and dependencies on earlier modules | the canonical event-stream read route, the commit/discard/replay contract, the append-only `TeachingEvent` schema, and before/after artifact refs must be named; the packet must call out dependency on both Teaching dialog transcript events and Before/after compare evidence refs | PENDING |
| No Trainer module is marked Lovable-ready without a confirmed BFF route | reviewer should confirm `Lovable readiness: false` is preserved for all four modules until BFF mutation routes are confirmed as implemented (not just planned) | PENDING |
| Lovable-readiness gate is explicit in the packet family | the delivery must not silently promote any Trainer module to Lovable-ready; each module's readiness state must be documented and justified | PENDING |

**AC-1 assessment (pre-implementation):** the backlog in
`pantheon-console-workbench-backlog.md` establishes the canonical BFF route names and
packetization prerequisites for each of the four Trainer Workbench modules. All four modules
currently show `Lovable readiness: no` with the explanation that the current shell is demo-grade
and only read-only teaching history exists under Persona Management. The parent reviewer must
confirm that explicit packet entries — not backlog-only narrative — exist for each module.

### AC-2: Teaching-history and trainer-specific flows are separated instead of hidden inside Persona screens

| Check | Evidence needed | Status |
|---|---|---|
| The packet family explicitly identifies the Persona-surface teaching-history reads as evidence inputs, not Trainer-owned surfaces | the packet or its BFF prerequisites section should state that `GET /api/v1/personas/{persona_id}/teaching` is a Persona drilldown, not a Trainer workflow route | PENDING |
| Teaching dialog is defined as a Trainer-owned workflow surface, not a Persona sub-screen | the packet must use `session_type=trainer` identity and Trainer-specific BFF routes rather than routing through the Persona Management BFF path | PENDING |
| Teaching replay is defined as a standalone Trainer surface, not a Persona Management sub-drilldown | the packet must name a standalone Trainer replay route rather than reusing the Persona management endpoint; the boundary between Persona-side evidence and Trainer-owned replay is explicitly documented | PENDING |
| The packet plan documents which flows remain in Persona screens vs. which flows move to Trainer-owned surfaces | a clear separation note should exist stating that teaching-history read surfaces in Persona Management are retained as evidence context, while session mutation, compare, and replay are Trainer-owned | PENDING |

**AC-2 assessment (pre-implementation):** the backlog explicitly notes that existing teaching-session
list surfaces sit inside Persona Management as drilldowns rather than as a standalone Trainer
packet family. The parent reviewer must confirm the packet plan enforces this boundary and does not
collapse teaching-history and trainer-workflow semantics into a single Persona sub-screen pattern.

---

## 3. Expected Artifact Inventory

The following artifacts should be delivered or updated when the parent task is complete. The reviewer
should confirm presence and confirm each artifact's role matches the description.

| Expected artifact | Expected role |
|---|---|
| A packet handoff file or section for `Teaching dialog` in `docs/pantheon-handoffs/` | Canonical screen packet covering session shell, transcript layout, event composer, session status copy, actor context display, and explicit BFF route prerequisites (`POST /api/v1/trainer/sessions`, `GET /api/v1/trainer/sessions/:id`, `POST /api/v1/trainer/sessions/:id/message`) |
| A packet handoff file or section for `Parameter controls` in `docs/pantheon-handoffs/` | Canonical screen packet covering editable control-state fields, patch confirmation, validation/warning states, and BFF prerequisites with Teaching dialog session dependency |
| A packet handoff file or section for `Before/after compare` in `docs/pantheon-handoffs/` | Canonical screen packet covering metric panels, control-state diff layout, warning hierarchy, degraded `preview_unavailable` copy, and BFF prerequisites with Parameter controls patch dependency |
| A packet handoff file or section for `Teaching replay` in `docs/pantheon-handoffs/` | Canonical screen packet covering history shell, event timeline, evidence drawer, commit/discard/replay action copy, `TeachingEvent` schema note, and BFF prerequisites with both Teaching dialog and Before/after compare dependencies |
| Updated summary table in `pantheon-console-workbench-backlog.md` for Trainer Workbench | The four Trainer rows should reflect packet status: if packets are now defined, the `Missing screen specs` column should be updated from `all screens` to the specific remaining gaps |
| A Trainer-vs-Persona separation note in the packet family | An explicit statement that teaching-history reads in Persona Management (`GET /api/v1/personas/{persona_id}/teaching`) are evidence context, while session mutation, compare, and replay are Trainer-owned surfaces |

> Note: the parent owner may deliver these as separate files or as sections within a single
> Trainer Workbench packet family document. The reviewer should evaluate substance, not enforce
> a specific file layout.

---

## 4. Dependency Map

### 4.1 Upstream dependencies already satisfied

| Dependency | Status | Relevance to BP5-WB-007 |
|---|---|---|
| `BP5-SVC-014` | done | The persona platform and consultation read surfaces establish the BFF path pattern and the canonical persona-identity model that the Trainer Workbench must align with; `session_type=trainer` identity must be compatible with the persona session model; the existing teaching-history read surfaces inside Persona Management belong to this service boundary |
| `BP5-SVC-009` | done | The telemetry ingest service and shock-absorption path establish the canonical event-log and write-path contract that the Trainer Workbench's `TeachingEvent` append-only schema and replay semantics must be compatible with; high-volume teaching replay events should not bypass the telemetry ingest shock-absorption layer |

### 4.2 Direct downstream unblocked by BP5-WB-007

| Task | How it depends on BP5-WB-007 |
|---|---|
| Future Lovable implementation task for Trainer Workbench screens | no Trainer screen may go to Lovable before BP5-WB-007 defines the canonical packet family; this task is the gate |
| Future BFF route definition slices for Trainer Workbench | explicit BFF route naming in the packet family (`POST /api/v1/trainer/sessions`, `POST /api/v1/trainer/sessions/:id/patch`, preview/rapid-eval route, event-stream read route) is the input to any BFF implementation slice; BP5-WB-007 establishes what needs to be built |
| Future TeachingEvent schema and replay-cursor formalization | the packet family's description of the append-only `TeachingEvent` schema and replay semantics is the input to any schema formalization or event-ordering slice |

### 4.3 Adjacent consumers that benefit once packet semantics are accepted

| Consumer | Benefit |
|---|---|
| Persona Workbench (BP5-WB-001 family) | Persona drilldown surfaces already expose teaching-history reads; canonical Trainer packet semantics clarify the boundary between Persona-owned drilldowns and Trainer-owned workflows, reducing the risk of UI surfaces overlapping |
| Research Workbench (BP5-WB-005 family) | Research experiments may cite teaching-session outputs as training evidence; canonical Trainer packet semantics establish a stable reference shape for cross-workbench experiment-to-teaching citations |
| Knowledge Workbench (BP5-WB-006 family) | Institutional memory and research notes may capture teaching outcomes; the Trainer packet family's BFF gap documentation informs how Knowledge modules can safely cite teaching evidence without depending on unimplemented Trainer routes |
| BFF / operator query layer | Once the Trainer Workbench BFF routes are named in the packet family, the BFF layer has explicit route targets rather than demo-grade shell direction |

### 4.4 Policy and backlog sources the parent reviewer must consult

| Source | Relevant sections | What to confirm |
|---|---|---|
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Trainer Workbench section: module descriptions, current state, packetization prerequisites, missing screen specs, BFF prerequisites table, Wave 3 internal ordering table | The four module packets match the content and prerequisites documented here; no module has been promoted to Lovable-ready without a confirmed BFF route; the Persona-vs-Trainer separation rule is preserved |
| `PERSONA_RUNTIME_MODEL.md` | Persona session and runtime instance model | The `session_type=trainer` identity constraint and trainer-session lifecycle must be compatible with the canonical persona session model; trainer sessions should not fork the persona session semantics without an explicit reconciliation note |
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Ingest shock-absorption and event-ordering policy | The `TeachingEvent` append-only schema and replay semantics should be compatible with the canonical telemetry event-ordering and batch-write contract established by BP5-SVC-009; the packet must not define a novel event-write path that bypasses the ingest layer |
| `OPENCLAW_RUNTIME_CONTRACT.md` | Adapter boundary and data passing contract | If any Trainer module routes session data or training signals through OpenClaw adapters, the packet should call out where the OpenClaw boundary sits and whether the trainer-session lifecycle interacts with the OpenClaw training loop |

---

## 5. Open Acceptance Questions for the Parent Reviewer

The following items are not blocking this sidecar's review but must be resolved during parent-task
review:

1. **Trainer session lifecycle vs. OpenClaw training loop** — the backlog notes the Trainer
   Workbench's teaching dialog must lock the trainer-session lifecycle and `session_type=trainer`
   identity. The reviewer should confirm whether the packet defines a concrete session lifecycle
   (states, transitions, terminal conditions) or whether it defers this to a future BFF
   implementation slice. If the training loop is driven by OpenClaw, the packet should document
   the OpenClaw boundary explicitly.

2. **Preview and rapid-eval route ownership** — the Before/after compare module depends on a
   preview or rapid-eval route. The reviewer should confirm whether the packet names a specific
   route and owner, or whether it marks this as a BFF gap that must be resolved before Lovable
   readiness. The backlog notes that only a demo preview/backtest refresh skeleton exists today.

3. **TeachingEvent schema formalization** — the Teaching replay module depends on an append-only
   `TeachingEvent` schema and event ordering/replay cursor semantics. The reviewer should confirm
   whether the packet provides enough schema detail to start a schema formalization slice, or
   whether it remains at the level of naming the gap. Compatibility with the telemetry ingest
   shock-absorption path (BP5-SVC-009) should be stated.

4. **Commit/discard/replay action semantics** — the Teaching replay module names commit, discard,
   and replay as supported actions. The reviewer should confirm whether the packet defines the
   mutation semantics (who can commit or discard, what is persisted vs. discarded, how replay
   differs from replaying a training signal) or defers them to a future BFF slice.

5. **Wave 3 sequencing boundary** — the backlog establishes Trainer Workbench as Wave 3, meaning
   it should not be handed to Lovable before Wave 2 Governance and Evolution follow-on surfaces have
   stable packet families (BP5-WB-003, BP5-WB-004). The reviewer should confirm whether the parent
   task's packet plan explicitly documents this Wave 3 sequencing constraint, or whether it advances
   to Lovable readiness without a wave-boundary rationale.

---

## 6. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No BFF route, service, or runtime implementation file was created or modified by this sidecar
- No Trainer Workbench screen packet was defined, modified, or superseded by this sidecar
- No workbench backlog entry was promoted to Lovable-ready by this sidecar
- The only artifact created by this slice is this reviewer packet
- Once reviewed and approved by `Codex`, this packet is available to the current parent owner and
  parent reviewer as a compact acceptance scaffold when the `BP5-WB-007` Trainer Workbench packet
  family is delivered
