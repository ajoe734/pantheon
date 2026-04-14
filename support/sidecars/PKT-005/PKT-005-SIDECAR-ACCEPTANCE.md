# PKT-005 Acceptance Packet (Sidecar)

**Task ID**: `PKT-005-SIDECAR-ACCEPTANCE`  
**Parent Task**: `PKT-005` — Packetize the global degradation banner and SSE/live reconciliation slice  
**Parent Owner**: Claude  
**Parent Reviewer**: Codex  
**Sidecar Owner**: Claude (auto-reassigned from Codex after Codex capacity failure 2026-04-14)  
**Sidecar Reviewer**: Codex (auto-reassigned from Qwen after repeated Qwen capacity/429 on 2026-04-14)  
**Helper Kind**: `acceptance_packet`  
**Generated**: 2026-04-14T14:00:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main runtime / registry / governance implementation.

## Current Packetization State

At the time this sidecar was prepared, the phase3 session has materialized `PKT-005`. The packet covers two cross-cutting Operator Console substrates:

1. **Global Degradation Banner** (`PKT-005-degradation-banner`) — derives state from `meta.staleness` and `meta.surfaces.*` present in every BFF composed view response; no dedicated endpoint required.
2. **SSE Reconciliation Substrate** (`PKT-005-sse-substrate`) — the live update layer built on three BFF-backed SSE streams that keeps all Operator Console screens in sync without full-page refreshes.

Both substrates are defined in the canonical packet-family document and backed by approved coordination response artifacts:

- `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml` — published 2026-04-14T13:30:00Z; status `published`
- `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml` — published 2026-04-14T13:30:00Z; status `published`

The `APP-002-W5-SSE-LIVE` sidecar (APPROVED, Qwen, 2026-04-12) is the primary upstream source for the SSE transport and reconciliation semantics.

This sidecar consolidates source evidence, maps the two substrates to their acceptance criteria, and documents the cross-cutting inheritance rules and downstream consumers.

Current parent-task review posture matters here: as of 2026-04-14, `PKT-005` itself is **not yet approved**. The remaining blocker is a residual sentence in `docs/screens/PKT-005-degradation-banner.md` that still says banner state updates on an "SSE snapshot event", which conflicts with the packet-family and SSE substrate rule that banner state changes only when a fresh BFF `meta` snapshot is received. This sidecar should therefore be read as support-only acceptance scaffolding, not as evidence that the parent packet has already cleared review.

---

## Source References

| Document | Role |
|---|---|
| `ai-status.json` | Live task registry for `PKT-005` and this sidecar |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Confirms `PKT-005` is an APP-002 packetization task depending on `LOOP-001` and `LOOP-003` |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-005-degradation-banner-sse-packet-family.md` | Canonical packet-family document for PKT-005; defines surface inventory, cross-cutting rules, and downstream consumers |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Operator Console section confirming degradation banner and SSE as Wave 1 deliverables |
| `support/sidecars/APP-002-W5-SSE-LIVE/APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` | APPROVED BFF + frontend handoff for the three SSE streams; wire format, replay, reconnect, and reconciler semantics |
| `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml` | Published contract-ready handoff for the degradation banner substrate |
| `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml` | Lovable UI task for the degradation banner |
| `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml` | Published contract-ready handoff for the SSE reconciliation substrate |
| `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml` | Lovable UI task for the SSE substrate |
| `docs/reviews/PKT-005-review-codex.md` | Current parent-task review report; records the remaining banner-vs-SSE authority conflict |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | L1 policy for degraded-control-plane operation and operator fallback — normative source for banner state semantics |

---

## 1. Acceptance Checklist For Parent Task `PKT-005`

This checklist is derived from the three `PKT-005` acceptance criteria in `ai-status.json` and `planning-session.json`.

### AC-1: Global degradation banner rules are defined once and referenced by all operator-facing packets

> `global degradation banner rules are defined once and referenced by all operator-facing packets`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 1.1 | Banner derives state exclusively from `meta.staleness` and `meta.surfaces.*` present in every BFF composed view response — no dedicated health-check endpoint | `PKT-005-degradation-banner-sse-packet-family.md` §Surface Inventory — "No dedicated endpoint required"; contract-ready yaml `endpoints: []` | ✅ Source-ready |
| 1.2 | Five banner variants are defined: `none`, `degraded`, `stale`, `partial`, `critical` | Same packet-family §Banner states table — each variant has a condition, variant name, and copy string | ✅ Source-ready |
| 1.3 | Per-surface `status` enum is restricted to `ok \| degraded \| unavailable`; `stale` and `partial` are not valid per-surface values | Same packet-family §Banner states — "The `stale` and `partial` values are not valid per-surface status values and must not appear in BFF responses" | ✅ Source-ready |
| 1.4 | Split-read screens (e.g. PKT-002 Incident Home) must merge all per-response `meta.surfaces` maps before passing to the banner component | Same packet-family §Cross-Cutting Inheritance Rules rule 1 — "For split-read screens: the UI layer merges `meta.surfaces` from all independent BFF responses" | ✅ Source-ready |
| 1.5 | Banner state must not be re-derived from SSE event payloads — SSE events do not carry `meta` snapshots | Same packet-family §Cross-Cutting Inheritance Rules rule 1 — "The banner decision is the backend's authority…SSE events do not carry `meta` snapshots" | ✅ Source-ready |
| 1.6 | `[Refresh now]` action in degraded/stale variants and `[Use admin CLI]` in stale variant are defined as copy requirements | Same packet-family §Banner states table — copy variants for each state | ✅ Source-ready |
| 1.7 | Contract-ready handoff published with `meta_fields_required` list and `front_actions_required` | `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml` — `meta_fields_required` covers `meta.snapshot_at`, `meta.staleness`, `meta.surfaces.<surface_key>.status` | ✅ Source-ready |
| 1.8 | Lovable UI task published | `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml` — exists and referenced from contract-ready artifact | ✅ Source-ready |

**Verdict**: AC-1 is source-ready. The global degradation banner rules are fully defined in the packet-family document and the contract-ready handoff. The parent packet can reference the published contract artifacts. No additional BFF endpoint work is needed.

---

### AC-2: SSE, reconnect, replay, and stale-state reconciliation are described as a frontend integration slice rather than pure visual work

> `SSE, reconnect, replay, and stale-state reconciliation are described as a frontend integration slice rather than pure visual work`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 2.1 | Three live SSE endpoints are identified with their event types | `PKT-005-degradation-banner-sse-packet-family.md` §SSE streams table — runtime events, incident events, kill-switch events | ✅ Source-ready |
| 2.2 | Replay semantics are defined: `?last_event_id=` on reconnect; full-buffer replay if `last_event_id` not in buffer; deque maxlen 500 | Same packet-family §Replay and reconnect; `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` §2.2 | ✅ Source-ready |
| 2.3 | Heartbeat comment (`: heartbeat`) every 30 s must not be treated as a data event | Same packet-family §Replay and reconnect | ✅ Source-ready |
| 2.4 | Reconnect manager must use exponential backoff: 1 s → 30 s with jitter | Same packet-family §Replay and reconnect; `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` §3 | ✅ Source-ready |
| 2.5 | SSE must not substitute for the initial BFF composed view fetch — events are applied on top of the initial response | Same packet-family §Cross-Cutting Inheritance Rules rule 4 — "SSE as incremental update, not initial fetch" | ✅ Source-ready |
| 2.6 | Idempotency guaranteed on reconnect replay: reconciler skips already-applied events by `id` | Same packet-family §Cross-Cutting Inheritance Rules rule 4; `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` §3.2 | ✅ Source-ready |
| 2.7 | Runtime stream `{runtime_id}` path caveat documented: BFF does not filter server-side; clients must filter by `data.runtime_id` | Same packet-family §Known caveats | ✅ Source-ready (non-blocking) |
| 2.8 | In-memory buffer caveat documented: BFF process restart drops replay history; clients must handle partial-buffer reconnect | Same packet-family §Known caveats | ✅ Source-ready (non-blocking) |
| 2.9 | Contract-ready handoff published with `front_actions_required` as an explicit integration checklist | `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml` — seven `front_actions_required` items covering client layer, idempotency, subscription lifecycle, initial fetch rule, and bff-gap trigger | ✅ Source-ready |

**Verdict**: AC-2 is source-ready. The SSE slice is explicitly described as a frontend integration concern with wire format, replay, reconnect, and reconciliation semantics. The packet correctly treats SSE as an integration substrate rather than a visual component specification.

Known non-blocking caveats (must carry forward into downstream packets):

| Caveat | Detail |
|---|---|
| Runtime stream path not filtered server-side | Clients must filter by `data.runtime_id`; server-side filtering is deferred |
| In-memory buffer drops on BFF restart | Clients must handle partial-buffer reconnect without assuming full replay |

---

### AC-3: Screen packets call out where live state is required and where polling fallback is acceptable

> `screen packets call out where live state is required and where polling fallback is acceptable`

| # | Verification Item | Evidence | Current Source Status |
|---|---|---|---|
| 3.1 | PKT-001 (Deployment Review Console) inherits degradation banner and subscribes to runtime SSE stream | `PKT-005-degradation-banner-sse-packet-family.md` §Relationship to Other Packets — `PKT-001` row | ✅ Source-ready |
| 3.2 | PKT-002 (Incident Response Console) inherits degradation banner and subscribes to incident and kill-switch SSE streams | Same §Relationship to Other Packets — `PKT-002` row | ✅ Source-ready |
| 3.3 | PKT-003 (Post-Incident and Evolution) inherits degradation banner; SSE subscription optional for read-only surfaces | Same §Relationship to Other Packets — `PKT-003` row — "SSE subscription optional for read-only surfaces" | ✅ Source-ready |
| 3.4 | PKT-004 (Persona Management) inherits degradation banner; SSE subscription optional | Same §Relationship to Other Packets — `PKT-004` row — "SSE subscription optional" | ✅ Source-ready |
| 3.5 | "Never show none" rule: a screen must never render empty-success state when data source is unavailable or degraded | Same §Cross-Cutting Inheritance Rules rule 3 | ✅ Source-ready |
| 3.6 | SSE subscription lifecycle rule: screens subscribe on mount, unsubscribe on unmount, using `SSEReconnectManager` | Same §Cross-Cutting Inheritance Rules rule 2 | ✅ Source-ready |

**Verdict**: AC-3 is source-ready. The relationship table in the packet-family document explicitly classifies downstream packets as required-SSE, optional-SSE, or banner-only. The "never show none" guard and subscription lifecycle rules provide the per-screen handoff contract.

---

## 2. Dependency Map

### 2.1 Formal Upstream Dependencies

`PKT-005` has two formal upstream dependencies:

```text
LOOP-001 -> PKT-005
LOOP-003 -> PKT-005
```

Both are `done` at the time of this sidecar.

Why they matter:

- `LOOP-001` stabilizes the `.coordination` loop and payload surface that PKT packets publish against.
- `LOOP-003` bootstraps front-repo prerequisites and mirror validation — hard dependency before screen packets are handed downstream to Lovable.

### 2.2 Packetization Anchors Inside PKT-005

| Anchor | Packet status | Source |
|---|---|---|
| Global Degradation Banner substrate | **ready** — contract-ready published, no dedicated endpoint needed | `meta.staleness` / `meta.surfaces.*` in every composed view (APPROVED) |
| SSE Reconciliation Substrate | **ready** — contract-ready published, three live BFF SSE endpoints backed by `APP-002-W5-SSE-LIVE` | `GET /api/v1/runtime/{runtime_id}/events/stream`, `GET /api/v1/incidents/stream`, `GET /api/v1/kill-switch/updates` (APPROVED) |
| Lovable UI task — degradation banner | **published** | `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml` |
| Lovable UI task — SSE substrate | **published** | `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml` |

### 2.3 Important Non-Dependencies

| Item | Why it is not a direct blocker for `PKT-005` | Why it still matters later |
|---|---|---|
| `EVO-004` execute boundary | PKT-005 covers read-only substrates (banner + SSE transport); no actionable mutation routing in scope | Evolution screens that render SSE-triggered state changes from evolution events must annotate freeze/rollback mutations as blocked on `EVO-004` |
| Operator Home and drift screens | Not in PKT-005 scope; those remain Wave 2 backlog items under `WB-001` | `WB-001` Operator Console backlog will inherit the PKT-005 substrate spec as a prerequisite |
| Server-side runtime stream filtering | Non-blocking for PKT-005 delivery; clients must filter client-side | Must be carried as a named caveat in all downstream packet specs that subscribe to the runtime SSE stream |
| BFF process restart drops SSE buffer | Non-blocking for PKT-005 delivery | Must be carried as a named caveat in downstream specs that depend on replay guarantees |

### 2.4 Downstream Consumers

Direct downstream consumers from the packet-family document:

```text
PKT-005 -> PKT-001  (Deployment Review Console — runtime SSE + banner)
PKT-005 -> PKT-002  (Incident Response Console — incident + kill-switch SSE + banner)
PKT-005 -> PKT-003  (Post-Incident and Evolution — banner; optional SSE)
PKT-005 -> PKT-004  (Persona Management — banner; optional SSE)
PKT-005 -> WB-001   (Operator Console backlog — substrate spec locked)
PKT-005 -> WB-008   (Evolution Workbench backlog — SSE substrate spec shared)
```

Additional expected consumers:

1. Lovable handoff for both substrates: `PKT-005-degradation-banner` and `PKT-005-sse-substrate` UI tasks are already published.
2. Any future workbench screen that inherits the degradation banner must use the `meta.staleness` / `meta.surfaces.*` derivation rule — not a dedicated health check.
3. Any future workbench screen that subscribes to SSE must use `SSEReconnectManager` and follow the four cross-cutting inheritance rules.

### 2.5 Reviewer Gates

Before the parent task `PKT-005` is accepted, the reviewer should confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Does the packet confirm that the Global Degradation Banner derives state from `meta.staleness` / `meta.surfaces.*` present in every existing composed view response — with no new dedicated endpoint added? | Yes — `endpoints: []` in the contract-ready artifact and explicit "no dedicated endpoint required" note in the packet-family document |
| G2 | Are all five banner variants (`none`, `degraded`, `stale`, `partial`, `critical`) defined with their triggering conditions and copy strings? | Yes — the banner states table in the packet-family document must map each condition to a variant and copy string |
| G3 | Does the packet describe the SSE substrate as a frontend integration slice (not just a visual component), with explicit wire format, replay semantics, reconnect manager spec, and reconciler idempotency rules? | Yes — AC-2 items 2.1–2.9 must all be visible in the parent packet or referenced by pointer |
| G4 | Does the relationship table classify downstream packets by required-SSE vs. optional-SSE vs. banner-only, and include the "never show none" guard? | Yes — PKT-001 required; PKT-002 required; PKT-003/PKT-004 optional; the guard must be stated |
| G5 | Are the two known non-blocking SSE caveats (server-side filtering deferred, buffer drops on restart) carried forward as named notes rather than hidden? | Yes — both caveats must appear as named non-blocking annotations in the parent packet |
| G6 | Does the packet avoid re-specifying surfaces already defined in PKT-001, PKT-002, PKT-003, and PKT-004? | Yes — PKT-005 defines only the shared banner substrate and SSE transport layer; composed view specs belong to the consuming packets |
| G7 | Do all PKT-005 support docs and screen specs align on backend authority for banner refresh, with no wording that implies SSE events directly update banner state? | Yes — `docs/screens/PKT-005-degradation-banner.md` must not say banner refresh occurs on an "SSE snapshot event"; the screen spec must match the packet-family and SSE substrate wording |

---

## 3. Support Notes

### 3.1 What This Sidecar Establishes

- Both `PKT-005` substrates are source-ready for immediate packetization. No missing BFF routes or missing L1 policy decisions block this work.
- The strongest source evidence is in `PKT-005-degradation-banner-sse-packet-family.md` (canonical packet definition) and `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` (APPROVED SSE BFF + reconciler handoff).
- The contract-ready coordination responses for both substrates are already published and can be referenced directly by the parent packet.
- Lovable UI tasks are already published for both substrates.
- The parent packet must use the five-variant banner state table as the authoritative classification, not a simplified pass/fail model.
- The parent packet must not reopen the SSE endpoint contract — the three streams are approved and live; the packet only upgrades the handoff language.
- The parent task still has one live review blocker in a support screen-spec file: remove the outdated "SSE snapshot event" banner-refresh wording so every PKT-005 document agrees that only fresh BFF `meta` snapshots can change banner state.

### 3.2 What This Sidecar Does Not Do

- It does not create the canonical PKT-005 packet-family artifact (that is already at `PKT-005-degradation-banner-sse-packet-family.md`).
- It does not add new BFF routes or modify any SSE transport code.
- It does not write screen-spec files for any downstream workbench screen.
- It does not modify APP-002 sidecars, BFF contracts, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, or any runtime code.
- It does not mark the parent task `PKT-005` itself as accepted.
- It does not re-specify surfaces owned by PKT-001, PKT-002, PKT-003, or PKT-004.

### 3.3 Review Posture

This sidecar supports approving the **support slice** immediately if the reviewer agrees with two core interpretations:

1. `PKT-005` succeeds on the degradation banner AC when it converts the existing APP-002 Frontend State Matrix §4 semantics into a canonical five-variant banner substrate with explicit meta-field derivation rules — no new BFF endpoint, no client-side re-derivation from SSE.
2. `PKT-005` succeeds on the SSE AC when it frames the three live streams as a frontend integration slice (not a visual feature) with explicit replay, reconnect, idempotency, and "initial fetch first" rules — carrying forward the two non-blocking caveats without silencing them.

Approving this sidecar does **not** imply approving the parent task. The sidecar is still useful as reviewer scaffolding while the parent packet remains in changes-requested state.

For the parent task, the reviewer should keep `PKT-005` open until the residual screen-spec conflict is fixed, and should reopen or keep it open if the eventual packet draft:

- merges the five banner variants into a simplified good/bad model
- re-derives banner state from SSE event payloads
- leaves any wording that says banner refresh happens on an "SSE snapshot event" instead of a fresh BFF `meta` snapshot
- adds a dedicated health-check endpoint that is not needed
- treats the SSE layer as a visual component rather than a shared integration substrate
- drops the two non-blocking SSE caveats
- re-specifies composed views that belong to sibling packets

---

## 4. Handoff Packet To Reviewer

**From**: Claude  
**To**: Codex  
**For**: `PKT-005-SIDECAR-ACCEPTANCE` review handoff record, and secondarily as scaffolding for parent task `PKT-005`

### Delivered In This Sidecar

1. A parent-task acceptance checklist tied to the three canonical `PKT-005` acceptance criteria.
2. A source-ready declaration for both substrates: degradation banner (no endpoint needed) and SSE reconciliation substrate (three approved live endpoints).
3. A cross-cutting inheritance rules summary: banner derivation, SSE subscription lifecycle, "never show none" guard, and "initial fetch first" rule.
4. A dependency map separating formal prerequisites (LOOP-001, LOOP-003 — both done) from non-blocking items (EVO-004 mutation boundary, SSE filter/buffer caveats).
5. A reviewer scaffold with seven gates tied to the AC requirements, including the remaining banner-authority consistency check.

### Recommended Review Outcome Logic

- Approve this sidecar if the packet is accurate and useful as support material for `PKT-005`.
- Sidecar approval should not be read as parent-task approval; `PKT-005` still needs one screen-spec sentence aligned with the backend-authority rule already adopted elsewhere in the packet family.
- For the parent task `PKT-005`, allow acceptance to proceed only after the packet draft (a) defines the degradation banner as a five-variant meta-derived substrate without a dedicated endpoint, (b) describes the SSE layer as a frontend integration slice with wire format, replay, reconnect, and reconciler rules, (c) classifies downstream packets by required/optional SSE and carries the two non-blocking SSE caveats, and (d) removes the outdated "SSE snapshot event" wording from the banner screen spec.
- Reopen or keep open the parent task if a future packet draft loses the five-variant banner table, leaves the banner-authority conflict unresolved, adds an unnecessary health-check endpoint, drops the SSE integration framing, or hides the two non-blocking caveats.

### Suggested Reviewer Comment For Parent Task

`PKT-005` should be accepted as a packet family once `docs/screens/PKT-005-degradation-banner.md` removes the outdated "SSE snapshot event" wording and the packet set fully agrees that the Global Degradation Banner is a cross-cutting, meta-derived substrate (five variants, no dedicated endpoint) while the SSE Reconciliation Substrate remains a frontend integration layer (three live streams, replay, reconnect manager, idempotent reconciler) with explicit downstream inheritance rules for PKT-001 through PKT-004.

---

*Prepared by Claude for the `PKT-005-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
