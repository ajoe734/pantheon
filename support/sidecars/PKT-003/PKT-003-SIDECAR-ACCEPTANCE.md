# PKT-003 Acceptance Packet (Sidecar)

**Task ID**: `PKT-003-SIDECAR-ACCEPTANCE`
**Parent Task**: `PKT-003` — Packetize Post-Incident and Evolution screens
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-14T11:15:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main runtime / registry / governance implementation.

## Current Packetization State

At the time this sidecar was prepared, the phase3 session has materialized `PKT-003`, and the Wave 3 BFF surfaces exist in the repo. The primary source of truth for post-incident and evolution read surfaces is `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` (approved 2026-04-11).

The key state split for PKT-003:

- **Read-only evidence panels**: backed by live BFF routes — post-incident composed view, evolution decisions, lineage, telemetry. These are packetizable now.
- **Actionable mutation review panels**: blocked on `EVO-004` execute-boundary formalization (action routing for freeze / rollback / retrain / redeploy follow-through is not yet finalized). `EVO-004` status: `todo`.
- **Inspiration Graph**: blocked — no BFF route or canonical backend data surface yet.

This sidecar consolidates the source evidence PKT-003 must absorb and keeps the EVO-004 boundary explicit for the parent owner and reviewer.

---

## Source References

| Document | Role |
|---|---|
| `ai-status.json` | Live task registry for `PKT-003` and this sidecar |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Source of the parent task title, dependencies, and acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Confirms `PKT-003` is Step 6 in APP-002 packetization and depends on `LOOP-001` plus `LOOP-003` |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Evolution Workbench section showing existing support vs. missing canonical specs |
| `support/sidecars/APP-002-W3-POSTINCIDENT-EVOLUTION/APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` | Primary source for Wave 3 EV/LN/TL read surfaces, composed post-incident view, operator journey, and BFF gating rules |
| `support/sidecars/EVO-004/EVO-004-SIDECAR-ACCEPTANCE.md` | Dependency-state and action-boundary map for the EVO-004 execute-boundary task that PKT-003 must keep visible |
| `support/sidecars/EVO-005/EVO-005-SIDECAR-ACCEPTANCE.md` | Kill-switch / safe-mode fast-path context; relevant to mutation review panel gating |

---

## 1. Acceptance Checklist For Parent Task `PKT-003`

This checklist is derived from the three `PKT-003` acceptance items in `ai-status.json` and `planning-session.json`.

### AC-1: Post-incident review, evolution review, lineage evidence, and telemetry evidence are mapped to packet-ready screens

> `post-incident review, evolution review, lineage evidence, and telemetry evidence are mapped to packet-ready screens`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 1.1 | The post-incident composed view already exists as one BFF-backed page-shaped response | `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` §2.2 — `GET /api/v1/operator/post-incident-review/{incident_id}` | ✅ Source-ready |
| 1.2 | The composed view composes incident + postmortem + evolution decisions + lineage edges + telemetry performance into one response with per-panel surface status | Same sidecar §§2.2, 4.3 | ✅ Source-ready |
| 1.3 | Evolution review surfaces (EV-01–EV-04) are all implemented | Same sidecar §2.1 table: `GET /api/v1/evolution-decisions`, `.../decisions/{id}`, `/api/v1/freeze-orders`, `/api/v1/rollbacks` | ✅ Source-ready |
| 1.4 | Lineage evidence surfaces (LN-01–LN-03) are all implemented | Same sidecar §2.1 table: `GET /api/v1/lineage`, `.../edges/{id}`, `.../graph` | ✅ Source-ready |
| 1.5 | Telemetry evidence surfaces (TL-01–TL-03) are all implemented | Same sidecar §2.1 table: `GET /api/v1/telemetry`, `.../summary`, `.../performance` | ✅ Source-ready |
| 1.6 | Operator journey from incident entry through post-incident review drill-down is defined | Same sidecar §3 | ✅ Source-ready |
| 1.7 | Per-panel UI gating rules are defined using `meta.surfaces.*.status` | Same sidecar §4.4 | ✅ Source-ready |
| 1.8 | Seed data IDs for smoke testing are documented | Same sidecar §2.3 (`inc-20260409-002`, `pm-20260409-002`, `evo-dec-001`, `ln-edge-001/002`, `artifact-042`) | ✅ Source-ready |

**Verdict**: AC-1 is source-ready. The W3 BFF sidecar provides complete source evidence for the four evidence panel families. The parent packet should merge this into one post-incident/evolution packet family without dropping the per-panel surface status gating.

Known BFF caveats that must remain visible in the parent packet (non-blocking but must not be silently dropped):

| Caveat | Detail |
|---|---|
| TL-01 filtering partial | `pool_id` and `time_range` accepted but ignored in v1 store |
| TL-02/TL-03 time_range ignored | Parameters accepted but not applied |
| EV-04 time_range deferred | Accepted in endpoint signature but not used |
| LN-03 `root_type` is no-op | Root type filtering requires registry metadata not yet available |
| Viewer role rejected | Requires `operator`/`approver`/`admin`/`reviewer` tokens |

### AC-2: EVO-004 dependency remains explicit where execute boundaries are still unresolved

> `EVO-004 dependency remains explicit where execute boundaries are still unresolved`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 2.1 | `EVO-004` is still `todo` at the time of this sidecar | `ai-status.json` task registry | ✅ Verified |
| 2.2 | `EVO-004` has four open action paths that are not yet formally bounded: freeze propagation, rollback trigger chain, retrain handoff, and redeploy follow-through | `EVO-004-SIDECAR-ACCEPTANCE.md` §2 Action-Boundary Map — all four paths are open | ✅ Verified |
| 2.3 | Mutation review panels (where an operator issues a freeze, rollback, retrain, or redeploy action from an evolution decision) cannot be packet-ready until `EVO-004` closes the execute boundary | `EVO-004-SIDECAR-ACCEPTANCE.md` §1.3 — `APP-002` and `EVO-005` are listed as downstream consumers of EVO-004 | ✅ Verified |
| 2.4 | The Evolution Center's read-only decision list and detail (`EV-01`, `EV-02`) are packetizable today without requiring `EVO-004` to be closed | `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` §2.1 | ✅ Verified |
| 2.5 | `EVO-004` not being closed is explicitly not a blocker for read-only evolution screen packetization | Same sidecar §5 caveats — gaps are non-blocking | ✅ Verified |

**Verdict**: AC-2 requires the parent packet to explicitly annotate each mutation review panel as "blocked on EVO-004 execute boundary" rather than silently omitting those panels or misrepresenting them as ready. The parent packet must name the four open action paths from `EVO-004-SIDECAR-ACCEPTANCE.md` §2 as the reason for the block.

### AC-3: The packet set distinguishes read-only evidence panels from future actionable mutation review panels

> `the packet set distinguishes read-only evidence panels from future actionable mutation review panels`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 3.1 | A clear classification boundary exists between read-only evidence panels and actionable mutation review panels | `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` §2.1–2.2: all W3 surfaces are read-only composed views | ✅ Source-ready |
| 3.2 | The Evolution Workbench backlog explicitly lists `Inspiration Graph` and `Mutation review` as missing canonical specs | `pantheon-console-workbench-backlog.md` → Evolution Workbench section | ✅ Source-ready |
| 3.3 | Read-only evidence panels can render using `meta.surfaces.*.status` and per-panel degraded-state copy | `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md` §4.4 | ✅ Source-ready |
| 3.4 | Actionable mutation panels (freeze, rollback, retrain, redeploy execution from evolution context) depend on `EVO-004` action routing, which is still open | `EVO-004-SIDECAR-ACCEPTANCE.md` §3 checklist — all A1–A10 still open | ✅ Verified |
| 3.5 | Kill-switch and safe-mode fast-path actions, if surfaced in the Evolution Workbench, must follow the boundary set by `EVO-005` (not this packet) | `EVO-005-SIDECAR-ACCEPTANCE.md` §1 — fast-path boundary is downstream of EVO-004 | ✅ Verified |

**Verdict**: AC-3 requires the parent packet to use an explicit two-tier screen inventory: (a) read-only evidence panels that are packet-ready now, and (b) actionable mutation panels that are blocked and must remain visibly blocked until `EVO-004` closes.

---

## 2. Dependency Map

### 2.1 Formal Upstream Dependencies

`PKT-003` has two formal upstream dependencies:

```text
LOOP-001 -> PKT-003
LOOP-003 -> PKT-003
```

Both are `done` at the time of this sidecar.

Why they matter:

- `execution-materialization.md` places `PKT-003` at Step 6, the third APP-002 packetization task after the closed-loop infra prerequisites.
- `LOOP-001` stabilizes the `.coordination` loop and payload surface that PKT packets publish against.
- `LOOP-003` bootstraps front-repo prerequisites and mirror validation — a hard dependency before screen packets are handed downstream.

### 2.2 Packetization Anchors Inside PKT-003

These are not separate task dependencies but the scope anchors the reviewer should validate:

| Anchor | Packet status | Source |
|---|---|---|
| `Post-Incident Review Console` | **ready** — composed BFF view exists | `GET /api/v1/operator/post-incident-review/{incident_id}` |
| `Evolution Decisions list/detail` | **ready** — EV-01/EV-02 implemented | `GET /api/v1/evolution-decisions` + `.../decisions/{id}` |
| `Freeze Orders list` | **ready** — EV-03 implemented | `GET /api/v1/freeze-orders` |
| `Rollbacks list` | **ready** — EV-04 implemented (time_range deferred) | `GET /api/v1/rollbacks` |
| `Lineage graph/edges` | **ready** — LN-01/LN-02/LN-03 implemented (root_type no-op) | `GET /api/v1/lineage/*` |
| `Telemetry evidence` | **ready** — TL-01/TL-02/TL-03 implemented (time_range deferred) | `GET /api/v1/telemetry/*` |
| `Inspiration Graph` | **blocked** — no BFF route or canonical backend data surface | Missing from current BFF; backlog item only |
| `Mutation Review panel` | **blocked** — depends on `EVO-004` execute-boundary formalization | `EVO-004-SIDECAR-ACCEPTANCE.md` §2 — four action paths still open |

### 2.3 Important Non-Dependencies

These are not blockers for creating the PKT-003 packet family itself, but must stay visible during review:

| Item | Why it is not a direct blocker for `PKT-003` | Why it still matters later |
|---|---|---|
| `EVO-004` not yet closed | Read-only evolution evidence surfaces can be packetized without the execute boundary | The parent packet must annotate the Mutation Review panel as explicitly blocked on EVO-004, not simply absent |
| Inspiration Graph missing BFF route | Post-incident and evolution read surfaces can ship without it | Evolution Workbench completeness (`WB-008`) depends on this screen eventually being addressed |
| Partial TL/LN filter coverage | The composed post-incident view works for the initial panel layout | Follow-up BFF work should be carried as named caveats in the packet, not hidden |
| `EVO-005` fast-path action contract | PKT-003 is read-heavy packetization; kill-switch / safe-mode actions are not part of the post-incident review UI | The action gating copy for degraded / unsafe states in the evolution context should point to the `EVO-005` boundary when available |
| Any new runtime / registry / governance implementation | This sidecar slice is support-only and the parent task is packet-definition work | Parent owner may later choose to absorb packet requirements into downstream implementation waves |

### 2.4 Downstream Consumers

Direct downstream consumers already materialized in planning:

```text
PKT-003 -> WB-001  (Operator Console backlog — post-incident module)
PKT-003 -> WB-008  (Evolution Workbench backlog)
```

Additional expected consumers:

1. Lovable handoff for the ready read-only screens: Post-Incident Review Console, Evolution Decisions, Lineage, and Telemetry panels.
2. Future backend tasks that add the Inspiration Graph BFF surface.
3. `EVO-004` outcome: once the execute boundary is locked, the Mutation Review panel can be upgraded from blocked to packet-ready.
4. `WB-008` Evolution Workbench backlog that must inherit this packet's two-tier read-only vs. blocked classification.

### 2.5 Reviewer Gates

Before the parent task `PKT-003` is accepted, the reviewer should confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Are the read-only evidence surfaces (post-incident, evolution decisions, lineage, telemetry) mapped to concrete BFF routes? | Yes, with existing W3 BFF routes as the source of truth |
| G2 | Are per-panel surface-status gating rules (`meta.surfaces.*.status`) carried into the packet? | Yes, `fresh/degraded/unavailable` states must be preserved for each evidence panel |
| G3 | Is `EVO-004` named explicitly as the reason Mutation Review panels are blocked? | Yes, and the four open action paths (freeze / rollback / retrain / redeploy) must be listed |
| G4 | Is the Inspiration Graph listed as a separate blocked screen with a named BFF gap, not silently omitted? | Yes, the missing route must be explicit |
| G5 | Does the packet avoid merging the read-only post-incident review surface with future mutation review into one ambiguous screen? | Yes, the two-tier classification must be maintained throughout |
| G6 | Are inherited W3 BFF caveats (deferred filters, no-op parameters) carried forward rather than hidden? | Yes, they are non-blocking but must not disappear from the packet record |

---

## 3. Support Notes

### 3.1 What This Sidecar Establishes

- `PKT-003` can be packetized for its read-only screens without waiting for `EVO-004` or the Inspiration Graph BFF.
- The strongest source evidence lives in `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md`, not in a to-be-written packet-family markdown file.
- The parent packet should inherit two explicit blocked annotations:
  - Mutation Review — blocked on `EVO-004` execute boundary
  - Inspiration Graph — blocked on missing BFF route
- The W3 BFF filter caveats (deferred time_range, no-op root_type, viewer role rejection) are non-blocking but must be carried forward as packet truth.

### 3.2 What This Sidecar Does Not Do

- It does not create the canonical `PKT-003` packet-family artifact.
- It does not close or partially satisfy `EVO-004`.
- It does not add an Inspiration Graph BFF route.
- It does not modify APP-002 sidecars, BFF contracts, or any runtime code.
- It does not mark the parent task `PKT-003` itself as accepted.

### 3.3 Review Posture

This sidecar supports approving the **support slice** immediately if the reviewer agrees with one core interpretation:

- `PKT-003` succeeds when it converts the existing W3 post-incident and evolution read-surface truth into a bounded Lovable-ready packet family, and keeps the `EVO-004` dependency and Inspiration Graph gap explicitly visible rather than pretending the full Evolution Workbench is ready.

For the parent task, the reviewer should reopen only if the eventual packet draft:

- drops the per-panel surface-status gating model
- hides the `EVO-004` execute-boundary dependency behind a vague "future work" note
- merges read-only evidence panels with actionable mutation review into one undifferentiated screen
- silently omits the Inspiration Graph blocked status
- drops the inherited W3 BFF filter caveats

---

## 4. Handoff Packet To Reviewer

**From**: Claude
**To**: Codex
**For**: `PKT-003-SIDECAR-ACCEPTANCE` review handoff record, and secondarily as scaffolding for parent task `PKT-003`

### Delivered In This Sidecar

1. A parent-task acceptance checklist tied to the three canonical `PKT-003` acceptance criteria.
2. A dependency map that separates formal prerequisites (LOOP-001, LOOP-003 — both done) from the live EVO-004 execute-boundary blocker.
3. A two-tier screen inventory distinguishing read-only evidence panels (packet-ready now) from blocked actionable mutation panels.
4. A reviewer scaffold with six gates tied to the AC requirements.

### Recommended Review Outcome Logic

- Approve this sidecar if the packet is accurate and useful as support material.
- For the parent task `PKT-003`, allow packetization to proceed once the packet draft maps the ready read-only screens to their concrete W3 BFF routes and keeps both blocked screens (Mutation Review, Inspiration Graph) explicitly annotated.
- Reopen the parent task only if a future packet draft loses the two-tier classification, drops the EVO-004 dependency annotation, or hides inherited BFF filter caveats.

### Suggested Reviewer Comment For Parent Task

`PKT-003` should be accepted as a packet family when it turns the existing W3 post-incident and evolution read surfaces into one bounded Lovable-ready handoff, keeps Mutation Review explicitly blocked on the `EVO-004` execute boundary, and records the Inspiration Graph as a named BFF gap — without pretending the full Evolution Workbench is ready today.

---

*Prepared by Claude for the `PKT-003-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
