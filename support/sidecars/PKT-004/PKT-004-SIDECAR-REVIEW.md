# PKT-004 Review Packet (Sidecar)

**Task ID**: `PKT-004-SIDECAR-REVIEW`  
**Parent Task**: `PKT-004` — Packetize Persona Management and Remaining Catalog drilldowns  
**Parent Owner**: `Qwen`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `planning_materialized` (no separate live `ai-status.json` execution row at review time)  
**Sidecar Owner**: `Claude` (auto-reassigned from Gemini after Gemini dispatch-pause 2026-04-14)  
**Sidecar Reviewer**: `Codex` (auto-reassigned from Gemini after repeated Gemini capacity/429 on 2026-04-14)  
**Helper Kind**: `review_packet`  
**Generated**: `2026-04-14T16:00:00Z`

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or the main runtime / registry / governance implementation.

Shared-truth sources used in this packet:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/pkt_004_sidecar_review.md`
- `ai-status.json` (live truth for this sidecar task; it does not currently carry a separate parent `PKT-004` execution row)
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- `support/sidecars/PKT-004/PKT-004-SIDECAR-ACCEPTANCE.md` (Prepared by Claude, status: done)
- `support/sidecars/APP-002-W4-PERSONA-MGMT/APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W4-REMAINING-CATALOG/APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md`

## 1. Current Snapshot

- Parent `PKT-004` is defined in `planning-session.json` with `Qwen` as owner and `Codex` as reviewer; the live durable status file currently tracks this sidecar review task rather than a separate parent `PKT-004` execution row.
- Both formal upstream dependencies — `LOOP-001` and `LOOP-003` — are `done`.
- The companion `PKT-004-SIDECAR-ACCEPTANCE` is `done` (Claude, 2026-04-14T12:30:00Z, approved by Codex). It provides the verified evidence framework for all three acceptance criteria.
- No canonical PKT-004 screen-spec or BFF-contract artifacts have been written yet (`docs/screens/PKT-004-*`, `docs/bff/PKT-004-*`, `.coordination/responses/PKT-004-*` are all absent).
- The primary source BFF surfaces that PKT-004 must packetize are:
  - `GET /api/v1/operator/persona-management/{persona_id}` — APPROVED in `APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md`
  - All 33 contractual read surfaces from `APP-002-W4-REMAINING-CATALOG` — absorbed and live
- Downstream `WB-002` (Persona Workbench backlog) depends on `PKT-004` completing.

## 2. Parent Acceptance Map

| Parent acceptance criterion | Evidence source | Status |
|---|---|---|
| Persona management is promoted from a sidecar handoff into a canonical screen packet | `PKT-004-SIDECAR-ACCEPTANCE.md` §1 AC-1: `GET /api/v1/operator/persona-management/{persona_id}` with `_require_read_role`, example payload, and Wave 2 annotation for missing Persona Workbench IA items verified against the APPROVED W4 persona-mgmt sidecar | ✅ Source-ready — no missing BFF routes block packetization |
| Remaining catalog endpoints are grouped into explicit drilldown modules instead of a vague catch-all | `PKT-004-SIDECAR-ACCEPTANCE.md` §1 AC-2: All 33 surfaces organized into six named modules (A — Persona Drilldowns, B — Capital/Binding Drilldowns, C — Deployment/Approval Drilldowns, D — Runtime Drilldowns, E — Telemetry/Lineage Drilldowns, F — Incident/Evolution Drilldowns) | ✅ Source-ready — six-module taxonomy established |
| Lovable readiness and missing screen-spec work are listed per module | `PKT-004-SIDECAR-ACCEPTANCE.md` §1 AC-3: Per-module readiness table with Wave 1 vs. Wave 2 classification; Tool profile and Consult policy explicitly deferred | ✅ Source-ready — per-module wave table established |

Working conclusion: all three acceptance criteria are source-ready. The blocker for `PKT-004` is canonical packet-artifact production (screen specs, BFF contracts, example payloads, coordination files), not missing backend surfaces.

## 3. Evidence Summary

### Source-Ready Surfaces

| Module | Surfaces | BFF Source | Packet-artifact status |
|---|---|---|---|
| **Persona management composed screen** | `GET /api/v1/operator/persona-management/{persona_id}` | `APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md` (APPROVED) | Screen spec + BFF contract not yet written |
| **A — Persona Drilldowns** | PS-01..PS-06 (6 surfaces) | `APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md` (absorbed) | Packet language for list shell and detail shell not yet written |
| **B — Capital/Binding Drilldowns** | CP-01..CP-04 (4 surfaces) | Same catalog sidecar | Selector/drawer packet spec not yet written |
| **C — Deployment/Approval Drilldowns** | DP-01..DP-04 (4 surfaces) | Same catalog sidecar | Shared with PKT-001 governance packet; standalone drilldown spec not yet written |
| **D — Runtime Drilldowns** | RT-01..RT-04 (4 surfaces) | Same catalog sidecar | Runtime status/rollback drilldown spec deferred to Wave 2 |
| **E — Telemetry/Lineage Drilldowns** | TL-01..TL-03, LN-01..LN-03 (6 surfaces) | Same catalog sidecar | Shared with PKT-003 consumers; drilldown spec deferred to Wave 2 |
| **F — Incident/Evolution Drilldowns** | IN-01..IN-05, EV-01..EV-04 (9 surfaces) | Same catalog sidecar | Shared with PKT-002/PKT-003 consumers; Wave 2 |

### Deferred / Blocked Items

| Item | Blocker | Recommended wave |
|---|---|---|
| Persona list shell screen spec | No canonical packet language written yet | Wave 2 |
| Persona detail shell screen spec | No canonical packet language written yet | Wave 2 |
| Tool profile BFF route | No BFF route defined | Wave 2 |
| Consult policy BFF route | No BFF route defined | Wave 2 |
| Runtime drilldown spec (Module D) | Packet language not written | Wave 2 |
| Telemetry / Lineage drilldown spec (Module E) | Shared with PKT-003; deferred filters in TL-01..TL-03 (time_range, pool_id not applied) | Wave 2 |
| Evolution / Incident drilldown spec (Module F) | Shared with PKT-002/PKT-003; EVO-004 mutation boundary unresolved for kill-switch and evolution drilldowns | Wave 2 |

### Inherited BFF Caveats (Must Carry Forward Into PKT-004 Canonical Artifacts)

| Caveat | Detail | Module affected |
|---|---|---|
| `snapshot` accepted but not enforced | `snapshot=preferred` returns `meta.snapshot_at` but does not align surface timestamps in v1 | Persona management composed screen |
| Read-surface staleness not tied to `BFF_READ_SURFACE_STATE` | Degradation flags only when a sub-surface returns `None` or empty | Persona management composed screen |
| `viewer` role rejected | Requires `operator`/`approver`/`admin`/`reviewer` token on all Wave 4 surfaces | All modules |
| `time_range` and `pool_id` deferred | TL-01 (`GET /api/v1/telemetry`) accepts but does not apply these filters in v1 | Module E |
| `root_type` no-op | LN-03 (`GET /api/v1/lineage/graph`) accepts `root_type` but graph is keyed by `root_id` only | Module E |
| `time_range` deferred on rollbacks | `GET /api/v1/rollbacks` accepts `time_range` but the v1 store does not apply it | Module F |

## 4. Reviewer Gates

Before the reviewer approves this sidecar (or the parent `PKT-004` canonical packet when it is produced), confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Is the persona management composed screen backed by an APPROVED BFF route with role-gating, example payload, and explicit Wave 2 annotation for missing IA items? | Yes — `GET /api/v1/operator/persona-management/{persona_id}` with `_require_read_role` verified in the W4 persona-mgmt sidecar |
| G2 | Are all 33 remaining catalog surfaces organized into six named modules (A–F) with individual endpoint and status listings? | Yes — six-module taxonomy established in `PKT-004-SIDECAR-ACCEPTANCE.md` §1 AC-2 |
| G3 | Does Module A (Persona Drilldowns) include all six PS surfaces and explicitly note that persona list shell and detail shell packet language are still missing? | Yes — surfaces PS-01..PS-06 listed; missing specs named as Wave 2 deferred |
| G4 | Does the per-module readiness table distinguish Wave 1 delivery items from Wave 2 deferred items? | Yes — Wave 1 (persona management, A, B, C) vs. Wave 2 (D, E, F; tool profile; consult policy) |
| G5 | Are non-blocking BFF caveats (deferred filters, no-op root_type, snapshot not enforced, viewer role rejection) carried as named non-blocking notes? | Yes — caveats documented in §3 of this packet; must reappear in canonical artifacts |
| G6 | Does the packet avoid re-specifying surfaces already owned by PKT-001 (deployment review, governance queue) and PKT-002/PKT-003 (incident, evolution composed views)? | Yes — Modules C, E, F reference sibling packets rather than duplicating composed-view specs |

## 5. Handoff Packet To Reviewer

**From**: Claude  
**To**: Codex  
**For**: `PKT-004-SIDECAR-REVIEW` reviewer inspection

### Delivered In This Sidecar

1. A current snapshot of the parent task state, confirming both upstream dependencies are done and no BFF surfaces block packetization.
2. A parent acceptance map verifying all three AC items are source-ready per the companion acceptance sidecar.
3. A surface-by-surface evidence table identifying ready modules, deferred/blocked items, and their recommended wave assignments.
4. Six inherited BFF caveats that must carry forward into canonical PKT-004 artifacts.
5. Six reviewer gates tied to the AC requirements for validating the eventual canonical packet.

### Recommended Review Outcome Logic

- **Approve this sidecar** if the evidence summary and reviewer gate framework are accurate and useful for supporting the parent task.
- **For the parent task `PKT-004`**, allow it to proceed to canonical artifact production once the packet draft:
  - (a) maps the persona management composed screen to the approved BFF route (`GET /api/v1/operator/persona-management/{persona_id}`) with role-gating and example payload
  - (b) organizes all 33 catalog surfaces into the six named drilldown modules (A–F) as defined in the acceptance sidecar
  - (c) uses an explicit per-module readiness table with Wave 1 / Wave 2 classification
  - (d) carries forward the six inherited BFF caveats
- **Reopen the parent task** only if a future packet draft collapses the six drilldown modules, drops Wave 1/Wave 2 classification, hides missing persona list/detail shell specs, or re-specifies surfaces owned by PKT-001/PKT-002/PKT-003.

---

*Prepared by Claude for the `PKT-004-SIDECAR-REVIEW` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
