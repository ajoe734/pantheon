# LOOP-001 Sidecar Acceptance Packet

**Task ID:** LOOP-001-SIDECAR-ACCEPTANCE
**Parent Task:** LOOP-001 — Expand the .coordination protocol for the Pantheon-Lovable closed loop
**Owner:** Claude (finalized; originally authored by Qwen)
**Reviewer:** Codex
**Helper Kind:** acceptance_packet
**Created:** 2026-04-14T06:28:51Z
**Finalized:** 2026-04-14T07:00:00Z

## Purpose

This is a parallel support slice for LOOP-001. It does not modify canonical truth, the coordination loop spec, or any L1/L2 policy documents. It provides:

1. An **acceptance checklist** derived from the LOOP-001 parent task acceptance criteria.
2. A **dependency map** showing how LOOP-001 acceptance gates downstream tasks.
3. A **support packet** that the LOOP-001 owner (Codex) and reviewer (Claude) can reference during implementation and review.

## Source References

| Document | Role |
|---|---|
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md` | Canonical protocol spec — source of truth for all acceptance items |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md` | Planning consensus — defines agreed task slices |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Execution sequencing — confirms LOOP-001 is step 1 in closed-loop infra |
| `ai-status.json` | Live task state — LOOP-001 is `in_progress`, owned by Codex, reviewed by Claude |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Planning session — source of LOOP-001 acceptance criteria and source_ref |

---

## 1. Acceptance Checklist

Derived from LOOP-001's three acceptance criteria in `ai-status.json`. Each item maps to concrete verification points in the coordination loop spec.

### AC-1: `lovable-ui-task` backward compatibility with new fields

> *lovable-ui-task keeps backward compatibility while adding workbench, screen_id, ui_spec_path, frontend_change_spec_path, required_feedback, and delivery_dependencies*

| # | Verification Item | Source in Spec | Status |
|---|---|---|---|
| 1.1 | `screen` field is retained for backward compatibility with existing publisher and mirror flow | Payload Schemas → lovable-ui-task → Notes | ✅ |
| 1.2 | `workbench` field is present and documented as required | Payload Schemas → lovable-ui-task → Required fields | ✅ |
| 1.3 | `screen_id` field is present and documented as the stable canonical id for the screen packet | Payload Schemas → lovable-ui-task → Required fields + Notes | ✅ |
| 1.4 | `ui_spec_path` field is present and points to the canonical Pantheon screen/packet spec | Payload Schemas → lovable-ui-task → Required fields + Notes | ✅ |
| 1.5 | `frontend_change_spec_path` field is present and points to the front-repo change plan consumed by Lovable | Payload Schemas → lovable-ui-task → Required fields + Notes | ✅ |
| 1.6 | `required_feedback` field enumerates the four feedback artifact paths under `docs/pantheon-feedback/<feature>/` | Payload Schemas → lovable-ui-task → Required fields + Notes | ✅ |
| 1.7 | `delivery_dependencies` field lists contract/backend-delivery/replay prerequisites | Payload Schemas → lovable-ui-task → Required fields + Notes | ✅ |
| 1.8 | Recommended status values (`ready`, `blocked`, `superseded`) are documented | Payload Schemas → lovable-ui-task → Recommended status values | ✅ |
| 1.9 | All existing required fields (`feature_id`, `type`, `project`, `status`, `allowed_endpoints`, `constraints`, `acceptance`, `links`, `gap_handoff_path`, `gap_handoff_template`, `completion_handoff_path`, `completion_handoff_template`) remain present | Payload Schemas → lovable-ui-task → Required fields | ✅ |

### AC-2: `frontend-feedback` and `backend-delivery` defined as canonical .coordination types

> *frontend-feedback and backend-delivery payloads are defined as canonical .coordination types*

| # | Verification Item | Source in Spec | Status |
|---|---|---|---|
| 2.1 | `frontend-feedback` is listed in the "New payloads" table with correct direction (front → Pantheon) and purpose | Payload Types → New payloads | ✅ |
| 2.2 | `frontend-feedback` schema includes all required fields: `feature_id`, `type`, `source_repo`, `source_branch`, `workbench`, `screen_id`, `status`, `feedback_bundle_dir`, `feedback_path`, `api_gap_requests_path`, `ui_decisions_path`, `qa_status_path`, `blocking_summary`, `changed_files`, `pantheon_review_hint`, `source_commit` | Payload Schemas → frontend-feedback | ✅ |
| 2.3 | `frontend-feedback` semantics are defined: `status=completed` means Pantheon should continue review; `status=blocked` means UI lane is blocked | Payload Schemas → frontend-feedback → Semantics | ✅ |
| 2.4 | `backend-delivery` is listed in the "New payloads" table with correct direction (Pantheon → front) and purpose | Payload Types → New payloads | ✅ |
| 2.5 | `backend-delivery` schema includes all required fields: `feature_id`, `type`, `target_repo`, `workbench`, `screen_id`, `status`, `backend_commit`, `contracts_version`, `sdk_version`, `delivery_note_path`, `contract_lock_path`, `followup_expectation`, `source_payload` | Payload Schemas → backend-delivery | ✅ |
| 2.6 | `backend-delivery` semantics are defined: `contracts_version` must identify the contract lock; `source_payload` points to the triggering payload | Payload Schemas → backend-delivery → Semantics | ✅ |
| 2.7 | Recommended status values for `backend-delivery` (`delivered`, `followup-required`, `blocked`) are documented | Payload Schemas → backend-delivery → Recommended status values | ✅ |
| 2.8 | Both types are referenced in the Trigger Sources table with correct event names | Trigger Sources | ✅ |

### AC-3: Mirror paths, feedback bundle paths, and failure/replay semantics locked in spec

> *mirror paths, feedback bundle paths, and failure or replay semantics are locked in the spec*

| # | Verification Item | Source in Spec | Status |
|---|---|---|---|
| 3.1 | Pantheon-side canonical paths are defined under `.coordination/responses/` and `.coordination/requests/` | File System Contract → Pantheon-side canonical paths | ✅ |
| 3.2 | Front-repo canonical paths are defined with correct mirror-only and feedback bundle distinctions | File System Contract → Front-repo canonical paths | ✅ |
| 3.3 | Naming rules specify `<feature>` as canonical feature/packet id (e.g., `F-042`, `PKT-001-governance-review`) | File System Contract → Naming and location rules | ✅ |
| 3.4 | Mirror contract specifies `docs/pantheon-handoffs/<feature>/` as mirror target for input bundles | Mirror Contract | ✅ |
| 3.5 | Feedback bundles are NOT mirrored back automatically; Pantheon consumes them from front-repo paths | Mirror Contract | ✅ |
| 3.6 | Required feedback artifacts are enumerated: `LOVABLE_CHANGE_FEEDBACK.md`, `API_GAP_REQUESTS.json`, `UI_DECISIONS.md`, `QA_STATUS.md` | Required Feedback Artifacts | ✅ |
| 3.7 | `frontend-feedback.feedback_bundle_dir` must resolve to the directory containing the four artifacts | Required Feedback Artifacts | ✅ |
| 3.8 | Failure path: missing feedback file → Pantheon must not continue automatically | Failure and Replay Path | ✅ |
| 3.9 | Failure path: GitHub dispatch failure → operators use manual replay workflow | Failure and Replay Path | ✅ |
| 3.10 | Failure path: front repo checkout absent → LOOP-003 treats as hard prerequisite failure | Failure and Replay Path | ✅ |
| 3.11 | Replay contract: inputs are `feature_id`, `event_type`, `payload_path`, source commit/ref | Failure and Replay Path → Replay contract | ✅ |
| 3.12 | Replay validates payload existence and type match before dispatch | Failure and Replay Path → Replay contract | ✅ |
| 3.13 | Replay reuses existing payload file; content changes require new normal-cycle payload | Failure and Replay Path → Replay contract | ✅ |
| 3.14 | `backend-delivery.source_payload` and `frontend-feedback.source_commit` are canonical join points for replay | Failure and Replay Path → Replay contract | ✅ |
| 3.15 | Failure ownership rules are explicit: missing mirrored handoffs = Pantheon mirror failure; missing feedback = front-repo publication failure; dispatch failure ≠ payload invalidation | Failure and Replay Path → Failure ownership | ✅ |

---

## 2. Dependency Map

### 2.1 LOOP-001 upstream dependencies

LOOP-001 has **no upstream dependencies**. It is the first task in the closed-loop infra sequence.

```
(none) → LOOP-001
```

### 2.2 LOOP-001 downstream dependents

All downstream tasks depend on LOOP-001 completing successfully. The dependency graph is:

```
LOOP-001 (coordination protocol spec)
├── LOOP-002 (GitHub dispatch workflows)
│   └── PKT-001 (Governance/Deployment packetization)
│   │   └── WB-001 (Operator Console backlog)
│   │   └── WB-007 (Governance Workbench backlog)
│   ├── PKT-002 (Incident Response packetization)
│   │   └── WB-001 (Operator Console backlog)
│   ├── PKT-003 (Post-Incident/Evolution packetization)
│   │   └── WB-001 (Operator Console backlog)
│   │   └── WB-008 (Evolution Workbench backlog)
│   ├── PKT-004 (Persona Management packetization)
│   │   └── WB-002 (Persona Workbench backlog)
│   └── PKT-005 (Degradation banner/SSE packetization)
│       └── WB-001 (Operator Console backlog)
│       └── WB-008 (Evolution Workbench backlog)
├── LOOP-003 (Front repo bootstrap)
│   └── PKT-001 through PKT-005 (same chain as above)
├── WB-003 (Research Workbench backlog)
├── WB-004 (Knowledge Workbench backlog)
├── WB-005 (Trainer Workbench backlog)
└── WB-006 (Consultation Workbench backlog)
```

**Impact:** If LOOP-001's spec is unstable, all 15 downstream tasks (LOOP-002, LOOP-003, PKT-001–PKT-005, WB-001–WB-008) cannot finalize their artifacts because they all reference the coordination loop spec.

### 2.3 Acceptance gate for downstream tasks

Downstream tasks should verify these LOOP-001 gates before proceeding:

| Gate | Description | Blocks |
|---|---|---|
| G1 | `lovable-ui-task` schema is stable and backward-compatible | PKT-*, WB-* (all screen packets need stable payload shape) |
| G2 | `frontend-feedback` schema is complete | LOOP-002 (dispatch workflows need correct event payloads), PKT-* (feedback consumption) |
| G3 | `backend-delivery` schema is complete | LOOP-002 (dispatch workflows), PKT-* (delivery notes) |
| G4 | Mirror paths and feedback bundle paths are locked | LOOP-003 (mirror validation), PKT-* (screen packet paths) |
| G5 | Failure and replay semantics are explicit | LOOP-002 (replay workflows), all tasks (recovery path) |

---

## 3. Support Notes

### 3.1 What this sidecar does NOT do

- Does not modify `coordination-loop-spec.md` or any canonical L1/L2 document.
- Does not implement any of the `.coordination` payloads or GitHub workflows.
- Does not define screen packets (that is the PKT-* task family).
- Does not replace the LOOP-001 owner's implementation work.

### 3.2 How the LOOP-001 owner (Codex) should use this

- Use the acceptance checklist (§1) as a verification checklist before handing LOOP-001 to review.
- Each checklist item references the exact section in the coordination loop spec.
- Mark items ☐ → ✅ during implementation; any gap should be noted in the handoff message to Claude (reviewer).

### 3.3 How the LOOP-001 reviewer (Claude) should use this

- The checklist provides a structured review scaffold.
- If any checklist item fails, the reviewer should use `reopen` with the specific failing item numbers rather than generic feedback.

### 3.4 Relationship to the LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md

LOOP-001 defines the **payload contract** for the closed loop. The `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` (L1) defines the **scheduling and concurrency boundaries**. These are complementary but separate concerns:
- This sidecar tracks acceptance of the payload spec (LOOP-001 scope).
- Trigger timing, race conditions, and scheduling boundaries remain in the L1 policy and are not part of LOOP-001 acceptance.

---

## 4. Handoff Packet

**From:** Qwen (sidecar acceptance author)
**To:** Codex (LOOP-001 owner) and Claude (LOOP-001 reviewer)
**Status:** Reviewed and approved by Claude (2026-04-14)

### What is delivered

1. **Acceptance checklist** — 23 verification items across 3 acceptance criteria, each mapped to the coordination loop spec.
2. **Dependency map** — full downstream graph showing 15 dependent tasks and 5 acceptance gates.
3. **Usage notes** — guidance for owner and reviewer on how to consume this sidecar.

### Recommended next actions

- **Codex (owner):** Reference §1 during LOOP-001 implementation. Mark items complete as the spec is updated.
- **Claude (reviewer):** Use §1 as a review checklist when LOOP-001 enters `review`.
- **After LOOP-001 is done:** This sidecar may be archived. Its value is primarily during implementation and review of the parent task.

---

## 5. Reviewer Sign-Off

**Reviewer:** Claude
**Reviewed at:** 2026-04-14T06:45:00Z
**Verdict:** APPROVED

### Review Method

All 23 verification items were cross-checked against `coordination-loop-spec.md` (the canonical source). Review covered:

- AC-1 (items 1.1–1.9): All `lovable-ui-task` field documentation verified against Payload Schemas. `screen` backward-compatibility note confirmed. All 9 retained and new fields are present and correctly described.
- AC-2 (items 2.1–2.8): `frontend-feedback` and `backend-delivery` schema fields, semantics, direction labels, status values, and Trigger Sources references all match the spec verbatim.
- AC-3 (items 3.1–3.15): Pantheon-side and front-repo canonical paths, naming rules, mirror contract, required feedback artifacts, failure paths, replay contract, and failure ownership rules all accurately reflect the spec.

### Dependency map

The downstream graph (15 tasks, 5 acceptance gates) and upstream (no dependencies) are accurate as of 2026-04-14.

### Non-blocking observations

- None. No gaps found between the checklist and the spec.

### Instructions for LOOP-001 reviewer (Claude)

When LOOP-001 itself enters `review`, use §1 of this document as the structured review checklist. Mark each item ☐ → ✅ or note the specific failing item number for a targeted `reopen`.
