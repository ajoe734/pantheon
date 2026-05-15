# LOOP-003 Sidecar Acceptance Packet

**Task ID:** LOOP-003-SIDECAR-ACCEPTANCE
**Parent Task:** LOOP-003 — Bootstrap the front repo prerequisites for the Pantheon-Lovable loop
**Owner:** Claude (helper-claimed while Codex is dispatch-paused)
**Reviewer:** Codex
**Helper Kind:** acceptance_packet
**Created:** 2026-04-14T15:45:00Z
**Parent Status:** `done` (archived at `2026-04-14T10:06:45Z`)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime, registry, or governance implementations.

## Purpose

This is a parallel support slice for LOOP-003. It provides:

1. An **acceptance checklist** derived from the LOOP-003 parent task acceptance criteria.
2. A **dependency map** showing how LOOP-003 acceptance gates downstream tasks.
3. A **support packet** referencing the delivered artifacts and review evidence.

Note: The parent task LOOP-003 is already archived as `done`. This sidecar backfills the missing acceptance packet that was expected in `ai-status.json`. Evidence is drawn from the live `coordination-loop-spec.md` and the archived delivery record in `ai-task-archive/tasks/LOOP-003.json`.

## Source References

| Document | Role |
|---|---|
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md` | Primary delivery artifact — canonical bootstrap prerequisites spec |
| `ai-task-archive/tasks/LOOP-003.json` | Archived parent delivery record (commit, handoffs, reviewer notes) |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Planning session — source of LOOP-003 acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md` | Accepted consensus packet — scope and lane assignments |
| `support/sidecars/LOOP-002/LOOP-002-SIDECAR-ACCEPTANCE.md` | Upstream sidecar — G3/G4 gate context inherited by LOOP-003 |

---

## 1. Acceptance Checklist

Derived from LOOP-003's three acceptance criteria in `ai-task-archive/tasks/LOOP-003.json` and `planning-session.json`.

### AC-1: `../front-ai-trading-system` prerequisite is recorded as a hard requirement

> *`../front-ai-trading-system` prerequisite is recorded as a hard requirement*

| # | Verification Item | Source / Evidence | Status |
|---|---|---|---|
| 1.1 | `coordination-loop-spec.md` contains a dedicated "Hard prerequisite: sibling checkout" section | `coordination-loop-spec.md` §Hard prerequisite: sibling checkout (lines 380–388) | ✅ |
| 1.2 | Section explicitly names `../front-ai-trading-system` as the required sibling path | `coordination-loop-spec.md:382` — "The directory `../front-ai-trading-system` … is a **hard prerequisite**" | ✅ |
| 1.3 | Section mandates that `coordination_repo_mirror.py` and all mirror validation steps **halt immediately** with a descriptive error if the sibling checkout is absent | `coordination-loop-spec.md:384` — "must halt immediately with a descriptive error. They must not proceed, silently skip, or produce partial output." | ✅ |
| 1.4 | Section specifies that a stale or partial clone is treated the same as absent (no silent partial execution) | `coordination-loop-spec.md:385` — "A stale or partial clone is treated the same as absent." | ✅ |
| 1.5 | Section provides operator fallback route: validation via GitHub Actions mirror workflow when local sibling checkout is unavailable | `coordination-loop-spec.md:386` — "Operators who cannot check out the front repo locally must route validation through the GitHub Actions mirror workflow" | ✅ |
| 1.6 | Section requires all CI validation and pre-dispatch checks to verify sibling checkout presence before executing mirror or validation steps | `coordination-loop-spec.md:387–388` | ✅ |
| 1.7 | Reviewer (Codex) confirmed in review notes that lines 382–389 satisfy AC-1 | `ai-task-archive/tasks/LOOP-003.json` `review_notes_zh[0]` | ✅ |

### AC-2: `pantheon-bus` and `coordination-bus` label bootstrap is specified

> *`pantheon-bus` and `coordination-bus` label bootstrap is specified*

| # | Verification Item | Source / Evidence | Status |
|---|---|---|---|
| 2.1 | `coordination-loop-spec.md` contains a dedicated "Label bootstrap" section | `coordination-loop-spec.md` §Label bootstrap (lines 391–410) | ✅ |
| 2.2 | `pantheon-bus` label is defined with color `#0075ca` and purpose "marks issues or PRs that carry cross-repo coordination bus events" | `coordination-loop-spec.md:397` | ✅ |
| 2.3 | `coordination-bus` label is defined with color `#e4e669` and purpose "marks issues or PRs that carry `.coordination` protocol messages or replay records" | `coordination-loop-spec.md:398` | ✅ |
| 2.4 | Labels are required to exist on **both** `pantheon` and `front-ai-trading-system` | `coordination-loop-spec.md:393` — "must exist on both the `pantheon` and `front-ai-trading-system` repositories" | ✅ |
| 2.5 | Bootstrap procedure step 1: verify labels exist using `gh label list` on both repos | `coordination-loop-spec.md:402` | ✅ |
| 2.6 | Bootstrap procedure step 2: `gh label create` commands provided for both labels on both repos | `coordination-loop-spec.md:403–409` | ✅ |
| 2.7 | Bootstrap procedure step 3: record label creation commit or API response as evidence in the loop bootstrap audit log | `coordination-loop-spec.md:410` | ✅ |
| 2.8 | Reviewer (Codex) confirmed in review notes that lines 393–417 satisfy AC-2 | `ai-task-archive/tasks/LOOP-003.json` `review_notes_zh[1]` | ✅ |

### AC-3: Mirror validation checklist exists for handoff bundle, request templates, and feedback bundle paths

> *mirror validation checklist exists for handoff bundle, request templates, and feedback bundle paths*

| # | Verification Item | Source / Evidence | Status |
|---|---|---|---|
| 3.1 | `coordination-loop-spec.md` contains a "Mirror validation checklist" section | `coordination-loop-spec.md` §Mirror validation checklist (lines 432–467 per reviewer notes) | ✅ |
| 3.2 | Checklist covers **handoff bundle** paths: `docs/pantheon-handoffs/<feature>/` in `front-ai-trading-system`, including the mirror target directory and all files listed in `lovable-ui-task.links` | `coordination-loop-spec.md` §Handoff bundle (mirror validation table, lines ~439–443) | ✅ |
| 3.3 | Checklist covers **Pantheon request templates** (example fixtures under `.coordination/requests/` and `.coordination/responses/`) | `coordination-loop-spec.md` §Request templates (Pantheon-side example fixtures) | ✅ |
| 3.4 | Checklist covers **front feedback bundle** paths: `LOVABLE_CHANGE_FEEDBACK.md`, `API_GAP_REQUESTS.json`, `UI_DECISIONS.md`, `QA_STATUS.md` | `coordination-loop-spec.md` §Front feedback bundle | ✅ |
| 3.5 | Checklist defines failure handling: missing required files must halt the operation (not silent skip) | `coordination-loop-spec.md` §Mirror validation checklist, failure handling rows | ✅ |
| 3.6 | Reviewer (Codex) confirmed in review notes that lines 432–467 satisfy AC-3 | `ai-task-archive/tasks/LOOP-003.json` `review_notes_zh[2]` | ✅ |

---

## 2. Dependency Map

### 2.1 LOOP-003 upstream dependencies

```
LOOP-001 (coordination protocol spec — done ✅)
    └── LOOP-002 (GitHub dispatch workflows — done ✅)
            └── LOOP-003 (Front repo bootstrap prerequisites — done ✅)
```

Note: Although LOOP-002 is not listed in `LOOP-003.depends_on` in the archive, the LOOP-002 sidecar acceptance packet (§2.2) identifies LOOP-003 as a direct downstream dependent. The bootstrap prerequisites in `coordination-loop-spec.md` presuppose the GitHub dispatch workflow specs (LOOP-002) are in place, because the bootstrap validation step requires all four workflows to be active.

### 2.2 LOOP-003 downstream dependents

LOOP-003 is a required prerequisite for all screen packet tasks and the full closed-loop execution path.

```
LOOP-003 (Front repo bootstrap prerequisites)
├── PKT-001 (Governance / Deployment packetization)
│   ├── WB-001 (Operator Console backlog)
│   └── WB-007 (Governance Workbench backlog)
├── PKT-002 (Incident Response packetization)
│   └── WB-001 (Operator Console backlog)
├── PKT-003 (Post-Incident / Evolution packetization)
│   ├── WB-001 (Operator Console backlog)
│   └── WB-008 (Evolution Workbench backlog)
├── PKT-004 (Persona Management packetization)
│   └── WB-002 (Persona Workbench backlog)
└── PKT-005 (Degradation banner / SSE packetization)
    ├── WB-001 (Operator Console backlog)
    └── WB-008 (Evolution Workbench backlog)
```

### 2.3 Acceptance gates for downstream tasks

| Gate | Description | Blocks |
|---|---|---|
| G-L3-1 | `../front-ai-trading-system` hard prerequisite is documented; halt-on-absent rule is in spec | All PKT-* tasks (any closed-loop cycle that triggers mirror or validation tooling) |
| G-L3-2 | `pantheon-bus` and `coordination-bus` labels are specified for both repos | Any live dispatch cycle (bus labels are required before legacy issue bus compatibility or audit mirroring is used) |
| G-L3-3 | Mirror validation checklist covers handoff bundle, request templates, and feedback bundle | Any `pantheon.contract_ready` dispatch that triggers the mirror + `lovable-ui-task` publication flow |
| G-L3-4 | Workflow prerequisite table is complete: all four workflows active across both repos before first live dispatch | First live `repository_dispatch` event in any PKT-* feature cycle |

**Notes on current gate status:**

- G-L3-1: Hard prerequisite is documented in `coordination-loop-spec.md:380–388`. Gate condition met in spec; operational enforcement depends on `coordination_repo_mirror.py` implementation (out of LOOP-003 scope).
- G-L3-2: Label bootstrap procedure is fully specified. Gate condition met in spec; operational enforcement requires human or CI execution of `gh label create` commands before first issue bus use.
- G-L3-3: Mirror validation checklist is complete. Gate condition met in spec.
- G-L3-4: Workflow prerequisite table references four active workflows (`coordination-dispatch-receiver.yml`, `coordination-manual-replay.yml`, `pantheon-handoff-receiver.yml`, `pantheon-feedback-publisher.yml`). Pantheon-side workflows deployed (LOOP-002 gate G1/G2). Front-repo template deployment is an operational step; spec requirement is met.

---

## 3. Residual Risks

| ID | Item | Blocking? |
|---|---|---|
| R1 | `coordination_repo_mirror.py` halt-on-absent behavior for missing sibling checkout is specified in the loop spec but not yet implemented. LOOP-003 scope is the spec; implementation belongs to a future execution slice. | No for this sidecar; preflight check needed before first mirror run |
| R2 | Label bootstrap (`pantheon-bus`, `coordination-bus`) has not been operationally executed. The spec is complete but the `gh label create` commands require a human or CI operator to run them before any legacy issue bus use. | No for this sidecar; operational prerequisite before issue bus use |
| R3 | Front-repo workflows (`pantheon-handoff-receiver.yml`, `pantheon-feedback-publisher.yml`) exist as templates in `.coordination/workflow-templates/` but have not been bootstrapped into the live `front-ai-trading-system` repo. This is a LOOP-003 spec requirement; actual deployment is an operational step or future automation slice. | No for this sidecar; required before first live dispatch cycle |
| R4 | Inherited from LOOP-002-SIDECAR-ACCEPTANCE R1: `.coordination/responses/F-042-contract-ready.yaml` uses `source_repo: pantheon` (shorthand) instead of `ajoe734/pantheon` (org-prefixed slug). Reconciliation is a preflight item before first live `pantheon.contract_ready` dispatch. | No for this sidecar; yes as preflight hygiene before live bootstrap |

---

## 4. What This Sidecar Does Not Do

- Does not modify `coordination-loop-spec.md` or any canonical L1/L2 document.
- Does not implement `coordination_repo_mirror.py` or any mirror tooling.
- Does not execute the label bootstrap procedure or deploy front-repo workflows.
- Does not replace the LOOP-003 owner's or reviewer's delivery record (archived in `ai-task-archive/tasks/LOOP-003.json`).
- Does not reopen the archived parent task `LOOP-003`.

---

## 5. Handoff Packet

**From:** Claude (helper-claimed sidecar owner)
**To:** Codex (sidecar reviewer)
**Status:** Ready for reviewer inspection

### What is delivered

1. **Acceptance checklist** — 21 verification items across 3 acceptance criteria (AC-1: 7 items, AC-2: 8 items, AC-3: 6 items), each mapped to specific line references in `coordination-loop-spec.md` and confirmed against the archived reviewer notes in `ai-task-archive/tasks/LOOP-003.json`.
2. **Dependency map** — full downstream graph showing 5 dependent PKT-* task families and 4 acceptance gates; gate status noted where determinable from spec evidence.
3. **Residual risk table** — 4 items (1 carried from LOOP-003 spec scope, 2 operational prerequisites, 1 inherited from LOOP-002 sidecar).

### Recommended next actions

- **Codex (reviewer):** Inspect §1 checklist items against `coordination-loop-spec.md` and the archived delivery record. If any item fails, use `reopen` with the specific failing item number. If all items pass, `approve` and return to owner for finalization.
- **After Codex approves:** Claude finalizes `LOOP-003-SIDECAR-ACCEPTANCE` to `done`.
- **Pre-bootstrap operators:** Before first live dispatch cycle, address R2 (label bootstrap), R3 (front-repo workflow deployment), and R4 (source_repo slug reconciliation).

---

*Generated by Claude as a sidecar `acceptance_packet` helper for `LOOP-003`. This file is a support artifact and does not modify canonical truth.*

---

## 6. Finalization Record

**Status:** `done`
**Finalized by:** Claude (owner)
**Finalized at:** 2026-04-15
**Review approved by:** Codex
**Review notes summary:** Checklist count reconciled to 21 items (AC-1: 7, AC-2: 8, AC-3: 6); support packet is consistent with coordination-loop-spec.md and ai-task-archive/tasks/LOOP-003.json. No canonical truth modified.

All three acceptance criteria verified:
- AC-1: Hard prerequisite for `../front-ai-trading-system` documented (7 items ✅)
- AC-2: Label bootstrap for `pantheon-bus` and `coordination-bus` specified (8 items ✅)
- AC-3: Mirror validation checklist covers handoff bundle, request templates, and feedback bundle (6 items ✅)

Task closed. Residual risks R1–R4 noted for pre-bootstrap operators; none block this sidecar's finalization.
