# PKT-002 Acceptance Packet (Sidecar)

**Task ID**: `PKT-002-SIDECAR-ACCEPTANCE`
**Parent Task**: `PKT-002` — Packetize Incident Response and Incident Control screens
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-14T10:48:57Z

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main runtime / registry / governance implementation.

## Current Packetization State

At the time this sidecar was prepared, the phase3 session already materialized `PKT-002`, but the repo does **not** yet contain:

- a canonical `PKT-002` packet-family markdown artifact
- a `.coordination/responses/lovable-ui-task-incident-response.yaml` packet

This sidecar is therefore a **pre-review acceptance scaffold** for the parent owner and reviewer. It consolidates the source evidence that `PKT-002` must absorb, and it keeps inherited W2 gaps visible instead of letting them disappear inside a future packet draft.

## Source References

| Document | Role |
|---|---|
| `ai-status.json` | Live task registry for `PKT-002` and this sidecar |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Source of the parent task title, dependencies, and acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Confirms `PKT-002` is Step 5 in APP-002 packetization and depends on `LOOP-001` plus `LOOP-003` |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Distinguishes `Incident Response Console` from broader `Operator Home` backlog surfaces |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/claude-readout.md` | Declares the intended PKT-002 output as the Lovable incident-response UI task packet |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/qwen-readout.md` | Adds the must-keep constraints on degraded-state gating and unavailable-data copy |
| `support/sidecars/APP-002-W2-READ-INCIDENT/APP-002-W2-READ-INCIDENT-SIDECAR-BFF-HANDOFF.md` | Primary source for incident composed-read surfaces, operator journey, and known read-side gaps |
| `support/sidecars/APP-002-W2-CONTROL-INCIDENT/APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` | Primary source for command submit/status flow, degraded control guidance, and action gating |
| `support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md` | Canonical frontend degraded-state matrix, including incident button gating and "never show empty-success" rule |
| `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md` | Higher-level module inventory showing how incident response fits the APP-002 frontend package |
| `support/sidecars/APP-002/APP-002-OPERATOR-ACTION-CONTRACT.md` | Shared operator command receipt and result-surface contract used by incident control |

---

## 1. Acceptance Checklist For Parent Task `PKT-002`

This checklist is derived from the three `PKT-002` acceptance items in `ai-status.json` and `planning-session.json`.

### AC-1: Incident response read and control surfaces share one packet family with explicit degraded-state and fallback rules

> `incident response read and control surfaces share one packet family with explicit degraded-state and fallback rules`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 1.1 | Incident read truth already exists as one composed view, not loose widget fragments | `APP-002-W2-READ-INCIDENT-SIDECAR-BFF-HANDOFF.md` §§2.1–2.3 | ✅ Source-ready |
| 1.2 | Incident control truth already exists as one action path around command submit, command poll, and degraded guidance | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §§2.1–3 | ✅ Source-ready |
| 1.3 | Frontend degraded-state handling is already defined for `fresh`, `degraded`, `stale`, `partial`, and `unavailable` | `APP-002-FRONTEND-STATE-MATRIX.md` §3.2.1 | ✅ Source-ready |
| 1.4 | Fallback behavior is explicit instead of implied; the UI must route operators to the secondary control path when data is unavailable | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §§3–4.5; `APP-002-FRONTEND-STATE-MATRIX.md` §3.2.1 | ✅ Source-ready |
| 1.5 | PKT-002 is required to preserve degraded-state button gating and unavailable-data language as packet truth, not optional UI polish | `qwen-readout.md` Slice 4 note for `PKT-002` | ✅ Source-ready |
| 1.6 | Known read/control caveats are documented and should remain visible in the parent packet rather than being flattened away | W2 read sidecar §3; W2 control sidecar §5 | ✅ Source-ready |

**Verdict**: AC-1 is source-ready. The parent packet does not need new semantics; it needs to merge the existing W2 read and control truths into one incident packet family without hiding degraded-state rules or inherited backend caveats.

### AC-2: Command receipts, action gating, and secondary control path copy are mapped to packet fields

> `command receipts, action gating, and secondary control path copy are mapped to packet fields`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 2.1 | The shared operator receipt model already exists with `command_id`, `tracking_url`, and poll timing metadata | `APP-002-OPERATOR-ACTION-CONTRACT.md` §2.2 | ✅ Source-ready |
| 2.2 | Incident control examples already show concrete `PauseRuntime`, `ExecuteRollback`, and `ActivateKillSwitch` request bodies | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §§4.1–4.3 | ✅ Source-ready |
| 2.3 | Incident control sidecar already includes a concrete command receipt example for packet field mapping | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §4.4 | ✅ Source-ready |
| 2.4 | Action gating rules are already explicit for stale, partial, and unavailable data states | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §4.5; `APP-002-FRONTEND-STATE-MATRIX.md` §3.2.1 | ✅ Source-ready |
| 2.5 | Secondary control path copy is already backed by a real guidance surface, not just prose | `GET /api/v1/operator/degraded-control-guidance` in W2 control sidecar §§2.1, 3, 4.5 | ✅ Source-ready |
| 2.6 | The packet must preserve the "never show empty-success when data is unreachable" rule for incident status and counts | `APP-002-FRONTEND-STATE-MATRIX.md` §3.2.1; `qwen-readout.md` Slice 4 note for `PKT-002` | ✅ Source-ready |

**Verdict**: AC-2 is source-ready. The parent packet should be judged on whether it converts these receipt, gating, and fallback semantics into explicit packet fields, not on whether the semantics still need to be invented.

### AC-3: Screen inventory distinguishes incident home, detail, and action drawer responsibilities

> `screen inventory distinguishes incident home, detail, and action drawer responsibilities`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 3.1 | The broader Operator Console backlog explicitly lists `Incident Response Console` as one screen family among other operator surfaces | `pantheon-console-workbench-backlog.md` → Operator Console section | ✅ Source-ready |
| 3.2 | Operator Home remains a separate missing screen-spec problem, so PKT-002 must not collapse incident work into the whole Operator Console | same backlog section | ✅ Source-ready |
| 3.3 | The incident detail screen already has a page-shaped layout with cards for status, runtime dashboard, kill-switch state, and control rail | `APP-002-FRONTEND-STATE-MATRIX.md` §3.2 | ✅ Source-ready |
| 3.4 | The action drawer / control rail responsibilities are already distinct from read-only incident detail responsibilities | `APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md` §§3–4.5; `APP-002-SIDECAR-BFF-HANDOFF.md` §5.1 | ✅ Source-ready |
| 3.5 | The list-or-home entry point already exists as incident locator flow (`GET /api/v1/incidents?status=active`) before opening the composed incident detail page | `APP-002-W2-READ-INCIDENT-SIDECAR-BFF-HANDOFF.md` §4 | ✅ Source-ready |

**Verdict**: AC-3 is source-ready. The parent packet should separate:

- incident home / entry routing
- incident detail composed view
- incident action drawer / control rail

It should not treat those as one undifferentiated Lovable page.

---

## 2. Dependency Map

### 2.1 Formal Upstream Dependencies

`PKT-002` has two formal upstream dependencies:

```text
LOOP-001 -> PKT-002
LOOP-003 -> PKT-002
```

Why they matter:

- `execution-materialization.md` places `PKT-002` at Step 5, inside APP-002 packetization after the closed-loop protocol and front-repo bootstrap prerequisites.
- `LOOP-001` stabilizes the `.coordination` payload shape that the future incident Lovable packet must publish against.
- `LOOP-003` keeps the front-repo checkout, label bootstrap, and mirror validation explicit before any new incident packet is handed off downstream.

### 2.2 Packetization Anchors Inside PKT-002

These are not separate task dependencies, but they are the real scope anchors the reviewer should validate:

| Anchor | Why it matters |
|---|---|
| Incident list / entry routing | The parent packet must show how operators find an incident before loading the detail surface, without pretending Operator Home is already fully specified |
| `GET /api/v1/operator/incident-response/{incident_id}` | Canonical composed detail view for the incident page body |
| `POST /api/v1/operator/commands` | Sole write entrypoint for pause / rollback / kill-switch actions |
| `GET /api/v1/operator/commands/{command_id}` | Command receipt polling surface that packet fields must expose |
| `GET /api/v1/operator/degraded-control-guidance` | Secondary control path copy and degraded fallback surface |
| APP-002 degraded-state matrix | Shared rule source for button gating and unavailable-state copy |

### 2.3 Important Non-Dependencies

These are not blockers for creating the PKT-002 packet family itself, but they must stay visible during review:

| Item | Why it is not a direct blocker for `PKT-002` | Why it still matters later |
|---|---|---|
| Missing `RT-03`, `TL-02`, and `EV-04` standalone endpoints | The composed incident-response view already exists and can still be packetized | The parent packet should carry these as inherited BFF caveats so later backend work can close parity cleanly |
| `rollback_action_type` not forwarded by `command_executor.py` | Incident control still has an authoritative command path for basic rollback | Advanced rollback-mode selection remains a follow-up if UI needs multiple rollback strategies |
| Operator Home dashboard and drift screens | They belong to broader `WB-001` backlog work, not this incident packet family | PKT-002 must avoid over-claiming that Operator Home is already packet-ready |
| Any new runtime / registry / governance implementation | This sidecar slice is support-only and the parent task is packet-definition work | Parent owner may later choose to absorb packet requirements into downstream implementation waves |

### 2.4 Downstream Consumers

The most direct downstream consumer already materialized in planning is:

```text
PKT-002 -> WB-001
```

Additional expected consumers:

1. The future incident Lovable handoff packet, expected by phase3 readouts as `.coordination/responses/lovable-ui-task-incident-response.yaml`.
2. Operator Console backlog refinement, where incident response must remain distinct from Operator Home and post-incident review.
3. Later backend follow-up slices that close the inherited read/control caveats without redefining the packet family.

### 2.5 Reviewer Gates

Before the parent task `PKT-002` is accepted, the reviewer should confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Does the packet use one shared degraded-state model across both read and control surfaces? | Yes, with `fresh/degraded/stale/partial/unavailable` semantics preserved |
| G2 | Are command receipt fields and control-path polling surfaces explicit in the packet? | Yes, `POST /commands`, `GET /commands/{id}`, and fallback guidance are all mapped |
| G3 | Does the packet preserve the secondary control path as a first-class fallback instead of a footnote? | Yes, unavailable and safety-critical states route to fallback copy explicitly |
| G4 | Does the packet avoid false empty-success states when data is unavailable? | Yes, unknown state is rendered as unknown, not as "0 incidents" or "all clear" |
| G5 | Does the screen inventory distinguish incident entry/home, incident detail, and action drawer responsibilities? | Yes, the packet keeps those boundaries visible and does not merge them into one vague console page |

---

## 3. Support Notes

### 3.1 What This Sidecar Establishes

- `PKT-002` can be packetized from existing APP-002 incident read/control sidecars without inventing new canonical semantics.
- The strongest source evidence lives in the W2 incident sidecars plus the global frontend state matrix, not in a yet-to-be-written packet-family markdown file.
- The parent packet should inherit the known backend caveats explicitly:
  - missing `RT-03`, `TL-02`, and `EV-04` standalone endpoints
  - rollback action-type forwarding gap
  - Operator Home not yet being the same thing as Incident Response Console

### 3.2 What This Sidecar Does Not Do

- It does not create the canonical `PKT-002` packet-family artifact.
- It does not create `.coordination/responses/lovable-ui-task-incident-response.yaml`.
- It does not modify APP-002 sidecars, BFF contracts, frontend-state truth, or any runtime code.
- It does not mark the parent task `PKT-002` itself as accepted.

### 3.3 Review Posture

This sidecar supports approving the **support slice** immediately if the reviewer agrees with one core interpretation:

- `PKT-002` is primarily a packetization and boundary-clarification task, not a mandate to close every inherited W2 BFF gap before the packet exists.

For the parent task, the reviewer should reopen only if the eventual packet draft:

- drops degraded-state rules
- hides secondary control path semantics
- merges Operator Home and Incident Response into one ambiguous surface
- or pretends inherited read/control caveats no longer exist

---

## 4. Handoff Packet To Reviewer

**From**: Codex
**To**: Claude
**For**: `PKT-002-SIDECAR-ACCEPTANCE` review handoff record, and secondarily as scaffolding for parent task `PKT-002`

### Delivered In This Sidecar

1. A parent-task acceptance checklist tied to the canonical phase3 PKT-002 acceptance criteria.
2. A dependency map that separates formal prerequisites from inherited W2 incident read/control gaps.
3. A reviewer scaffold that keeps incident entry routing, incident detail, control rail, and degraded fallback boundaries explicit.

### Recommended Review Outcome Logic

- Approve this sidecar if the packet is accurate and useful as support material.
- For the parent task `PKT-002`, allow packetization to proceed once the packet draft accurately packages the existing incident read/control truth and keeps the inherited caveats explicit.
- Reopen the parent task only if a future packet draft loses the degraded-state model, omits secondary control path copy, or blurs incident home/detail/action boundaries.

### Suggested Reviewer Comment For Parent Task

`PKT-002` should be accepted as a packet family when it turns the existing W2 incident read/control sidecars into one bounded Lovable-ready handoff without erasing degraded-state gating, command receipt semantics, or the distinction between incident entry, detail, and action-drawer responsibilities.

---

*Prepared by Codex for the `PKT-002-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
