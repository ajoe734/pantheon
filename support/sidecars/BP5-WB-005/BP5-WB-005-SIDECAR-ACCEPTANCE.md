# BP5-WB-005 — Acceptance Packet and Dependency Map

- Sidecar ID: `BP5-WB-005-SIDECAR-ACCEPTANCE`
- Parent task: `BP5-WB-005` — Packetize the Research Workbench family
- Helper kind: `acceptance_packet`
- Prepared by: Claude
- Date: 2026-04-16
- Reviewer: Codex
- Status: accepted / done

---

## Purpose

This packet formalizes the acceptance checklist, dependency map, and handoff summary for `BP5-WB-005`. It is a support artifact only — no canonical truth files were modified during its preparation.

---

## Acceptance Criteria Checklist

| Criterion | Status | Evidence |
|---|---|---|
| Each Research Workbench module has a packet with surface scope and internal ordering | **met** | `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` — all five modules (RW-01 through RW-05) have named screen surfaces, lifecycle semantics, and per-module wave ordering |
| Each module has a backend dependency matrix | **met** | PACKET_FAMILY.md sections RW-01 through RW-05 each contain a `### Backend gaps` table listing every missing route and contract; 14 missing routes and 4 missing contracts across the family |
| Per-module Lovable readiness gate is explicit and module-scoped | **met** | Each module section ends with a `### Lovable readiness gate` entry; gate is `false` for all five modules; matrix header states readiness is per-module, not a family-wide all-18-gaps gate |
| Lovable readiness stays false until BFF routes and read models are explicit | **met** | No module is marked ready; promotion criteria (PACKET_FAMILY.md) require per-module BFF route implementation before any module can advance |
| Internal ordering respects upstream data dependencies | **met** | Wave ordering: RW-01 first (foundational entity), RW-02 and RW-03 depend on RW-01, RW-04 depends on RW-01 and RW-03, RW-05 depends on RW-04 versioned artifact refs |
| Codex review passed with no remaining blocking findings | **met** | `docs/pantheon-handoffs/RW-005-research-workbench/REVIEW.md` — APPROVED on re-review (2026-04-16); three findings raised and verified resolved |

---

## Dependency Map

### Upstream task dependencies

| Dependency task | Status | What it provides to BP5-WB-005 |
|---|---|---|
| `BP5-SVC-005` — Deployment orchestration saga with outbox/inbox consistency | **done** | Stable cross-service saga and outbox/inbox consistency contract; experiment launch and run-status polling inherit this contract; PACKET_FAMILY.md places Experiment Launch (RW-04) in Wave 3 and withholds Lovable readiness until BFF routes are live |
| `BP5-SVC-014` — Persona platform and consultation read surfaces | **done** | Canonical persona identity and BFF path; research ticket ownership and experiment lineage attribution rely on this; all five modules are guarded behind BFF route completeness rather than persona-screen proximity |

Both upstream dependencies are **done**. No unmet dependency blocks packet family closure.

### Internal module ordering (Wave 3)

```
RW-01 Research Ticket        ← foundational entity; must land first
  └─ RW-02 Search            ← depends on RW-01 ticket read model (linked_ticket_id)
  └─ RW-03 Analyze           ← depends on RW-01 ticket/experiment binding
       └─ RW-04 Experiment Launch  ← depends on RW-01 and RW-03
            └─ RW-05 Artifact Compare  ← depends on RW-04 versioned artifact refs
```

### Backend gap summary by module

| Module | Missing routes | Missing contracts |
|---|---|---|
| RW-01 Research Ticket | 4 | — |
| RW-02 Search | 1 | 1 (search index adapter) |
| RW-03 Analyze | 2 | 1 (metric aggregation endpoint) |
| RW-04 Experiment Launch | 4 | 1 (experiment state machine contract) |
| RW-05 Artifact Compare | 3 | 1 (artifact versioning semantics) |
| **Total** | **14 routes** | **4 contracts** |

Critical path note: `GET /api/v1/research/tickets` blocks RW-01, RW-02, RW-03, and RW-04. `GET /api/v1/experiments/{experiment_id}` blocks RW-04 and RW-05. Resolving Research Ticket routes first unlocks the widest downstream surface area.

---

## Artifact Inventory

| Artifact | Location | Status |
|---|---|---|
| Canonical packet family | `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` | complete, APPROVED by Codex |
| Codex review record | `docs/pantheon-handoffs/RW-005-research-workbench/REVIEW.md` | APPROVED on re-review (2026-04-16) |
| Review sidecar | `support/sidecars/BP5-WB-005/BP5-WB-005-SIDECAR-REVIEW.md` | complete |
| This acceptance packet | `support/sidecars/BP5-WB-005/BP5-WB-005-SIDECAR-ACCEPTANCE.md` | ready for review |

---

## Codex Review Findings (resolved)

Three findings were raised during the Codex review and verified resolved before APPROVED was recorded:

| Finding | Severity | Resolution |
|---|---|---|
| Matrix header used a family-wide gate instead of per-module readiness | High | Fixed — matrix header now states a module becomes Lovable-ready when its own rows and upstream prerequisites are resolved |
| Lifecycle token `in_progress` diverged from backlog canonical `in-progress` | Medium | Fixed — tokens restored to `open → in-progress → closed → archived` in both surface scope and prerequisite text |
| RW-05 upstream dependency incorrectly pulled `GET /api/v1/artifacts` into RW-04's dependency | Medium | Fixed — upstream dependency scoped to Launch-produced versioned artifact refs; registry route stays in RW-05 backend gaps |

---

## Canonical Truth Verification

- No L1 policy file was modified.
- No canonical contract, registry, or governance file was modified.
- No runtime implementation file was modified.
- All work is confined to support artifacts and the packet family handoff directory.

---

## Open Forward-Looking Items

None blocking closure. The following items are scoped to the Wave 3 BFF implementation window:

1. Define canonical field shapes and example payloads for each module's primary read surface (per PACKET_FAMILY.md promotion criterion 4). These are implementation-time artifacts, not packetization artifacts.
2. Wire `PKT-005` degradation banner and SSE substrate inheritance into each module screen spec when the module's BFF routes go live (noted in PACKET_FAMILY.md Canonical References section).

---

## Owner Closeout Note (2026-04-16)

Codex reviewed and approved this packet on 2026-04-16 (review_notes_zh: 已核對 PACKET_FAMILY.md、REVIEW.md、planning-session.json 與相關路徑 git status；內容一致且本次 sidecar 只新增 support artifact). Owner final checks complete:

1. Acceptance criteria checklist verified accurate against `PACKET_FAMILY.md` and `REVIEW.md` — all six criteria met.
2. Dependency map correctly reflects both upstream contracts (BP5-SVC-005 done, BP5-SVC-014 done).
3. No canonical file was modified — all work confined to support artifacts and the packet family handoff directory.

Sidecar task closed. Parent task `BP5-WB-005` may now absorb or archive this packet at owner discretion.
