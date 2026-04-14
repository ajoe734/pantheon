# BG-006 Acceptance Packet (Sidecar)

**Parent Task**: `BG-006` — Publish operator acceptance matrix across BFF, internal API, CLI, and fallback paths  
**Parent Owner**: `Claude`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `in_progress`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Qwen`  
**Helper Kind**: `acceptance_packet`  
**Generated**: 2026-04-13T15:00:00Z  
**Last Updated**: 2026-04-14T00:20:15Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations.

Finalize refresh note (2026-04-14):

- This packet reflects the current durable state after `docs/reviews/BG-006-review-codex.md` requested changes on `2026-04-13T14:37:12Z`.
- Earlier support material at `support/sidecars/BG-006/BG-006-SIDECAR-REVIEW.md` correctly summarized the then-current review pass, but it no longer describes the parent task's current execution state.
- Durable `ai-status.json` now shows `BG-006` as `in_progress`, owned by `Claude`, reviewed by `Codex`, with the reopen reason pointing to CLI/internal fallback truth and degraded routing regressions in the root `OPERATOR_ACCEPTANCE_MATRIX.md`.
- The task-scoped brief for this resumed pass marks `BG-006-SIDECAR-ACCEPTANCE` as `review_approved`, owned by `Codex`, reviewed by `Qwen`, and waiting for owner-close.
- The packet's substantive verdict is unchanged: the parent task is planning-unblocked but still not acceptance-ready until the root matrix repairs are absorbed by the parent owner.

Shared-truth and task-scoped sources used in this packet:

- `AI_COLLABORATION_GUIDE.md` — lifecycle, sidecar boundary, and durable-state rules
- `.orchestrator/task-briefs/bg_006_sidecar_acceptance.md` — task-scoped scope guardrails for this slice
- `ai-status.json` — durable task state, owner/reviewer assignment, handoff trail, and sidecar scope
- `docs/02-architecture/consensus/phase2/planning-session.json` — materialized BG-006 task definition
- `docs/02-architecture/consensus/phase2/gap-response-matrix.md` — GAP-06 rationale and "packaging not capability" framing
- `Pantheon_Blueprint_Gap_Review_v1.md` — GAP-06 minimum matrix fields and required acceptance evidence
- `OPERATOR_ACCEPTANCE_MATRIX.md` — current parent artifact under repair
- `docs/reviews/BG-006-review-codex.md` — current blocking review findings
- `docs/reviews/BG-006-review-gemini.md` — earlier approval context before the current reopen
- `docs/02-architecture/consensus/phase2/OPERATOR_ACCEPTANCE_MATRIX.md` — repo-true operator acceptance reference showing real CLI/internal fallback coverage
- `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` — degraded-path and per-surface fallback contract
- `tools/pantheon_admin/cli.py` — implemented CLI fallback coverage
- `services/control_plane/internal_api.py` — implemented internal API fallback coverage

---

## 1. Dependency Map

### 1.1 Parent Dependencies and Current Execution Truth

Hard dependency:

- `PLAN-002` is `done` in `ai-status.json`; the planning session that materialized `BG-006` is accepted and human-approved.

Execution-state note:

- `planning-session.json` materialized `BG-006` as a phase2 blueprint-gap task, but durable execution ownership has since been operationally reassigned.
- For current execution and close readiness, `ai-status.json` is the source of truth: `BG-006` is now `in_progress`, owned by `Claude`, reviewed by `Codex`.

### 1.2 What BG-006 Was Supposed to Deliver

The phase2 planning materials and gap review converge on one P1 packaging task: publish a single operator acceptance matrix that classifies real surfaces and degraded paths without redefining the runtime model.

Expected deliverables:

| Deliverable | Evidence |
|---|---|
| Single operator acceptance matrix | `planning-session.json` defines BG-006 as "Publish operator acceptance matrix across BFF, internal API, CLI, and fallback paths" |
| GAP-06 minimum fields | `Pantheon_Blueprint_Gap_Review_v1.md` requires `surface name`, `canonical object`, `authoritative / composed / fallback / support-only`, `degraded behavior`, `required permissions`, `test status`, and `operator drill status` |
| Acceptance-evidence backlog | `Pantheon_Blueprint_Gap_Review_v1.md` also requires `operator acceptance script`, `degraded mode drill`, `CLI fallback drill`, `BFF down scenario drill`, and `lovable/front repo cutover confirmation` |
| Packaging, not greenfield implementation | `gap-response-matrix.md` says GAP-06 is mostly an acceptance-packaging gap and must reuse existing BFF inventory, degraded-path rules, and fallback capabilities rather than inventing new surfaces |

### 1.3 Current Parent Evidence Snapshot

| Item | Current State | Assessment |
|---|---|---|
| Primary artifact exists | Root `OPERATOR_ACCEPTANCE_MATRIX.md` exists on disk | ✅ Present |
| GAP-06 structural shape exists | The root matrix still enumerates five surfaces and includes the required columns | ✅ Structure present |
| Real CLI fallback implementation exists | `tools/pantheon_admin/cli.py` covers deployment approve/reject, runtime pause/resume/force-halt, rollback execute/list/abort, and kill-switch activate/deactivate/status | ✅ Capability present |
| Real internal API fallback implementation exists | `services/control_plane/internal_api.py` exposes deployment, runtime pause/resume, rollback, and kill-switch endpoints | ✅ Capability present |
| Root matrix matches repo-truth CLI status | Current root matrix still marks key `S-CLI` deployment/runtime/kill-switch rows as `not implemented` | ❌ Blocking mismatch |
| Root matrix matches degraded routing truth | Current root matrix routes BFF-down pause/rollback to `S-EMRG` instead of the real `S-IAPI` / `S-CLI` fallback split | ❌ Blocking mismatch |
| Root matrix matches degraded read-surface truth | Current runtime-manager outage row says `S-BFF` read is unaffected, which is stronger than the per-surface degradation contract | ❌ Medium mismatch |
| Current reviewer state is approval-ready | Durable `ai-status.json` shows reopened `in_progress` with Codex changes requested | ⏳ Not acceptance-ready |
| Parent metadata is close-ready | `ai-status.json` still lists `BG-006` with `artifacts: []` and `acceptance: []` | ⏳ Owner-close cleanup still needed |

### 1.4 Downstream / Adjacent Consumers

| Consumer | Type | Why BG-006 matters |
|---|---|---|
| Operator runbooks and drill planning | Operational | Drill and outage guidance must point operators to the correct fallback surfaces |
| BFF / operator-facing product wording | Product surface | Degraded-mode copy must distinguish composed BFF flows from authoritative and fallback paths |
| Future reviewer / acceptance packets | Support workflow | Support artifacts need one repo-true statement of operator path authority instead of re-deriving it from scattered contracts |

### 1.5 Readiness Verdict

**BG-006 is planning-unblocked but not acceptance-ready.**

What is already true:

- The task exists for real and is hard-unblocked by `PLAN-002`.
- The parent artifact already has the required matrix shape for GAP-06.
- The repo already contains real CLI and internal API fallback implementations, so this is not a capability-gap task.

What still blocks parent closure:

- the root matrix still downgrades implemented CLI/internal fallback paths to `not implemented`
- BFF-outage routing still sends pause/rollback to `S-EMRG` instead of the actual `S-IAPI` / `S-CLI` fallback paths
- the runtime-manager outage summary overstates unaffected BFF runtime reads instead of preserving per-surface degradation truth
- the acceptance evidence table and non-blocking backlog still describe existing CLI fallback as future work
- the parent task metadata has not yet been updated to record its actual artifact path and acceptance expectations

---

## 2. Acceptance Checklist for Parent Task (`BG-006`)

This checklist converts the planning title and the current reopen findings into a close instrument for the parent owner.

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `matrix_doc_exists` | Root `OPERATOR_ACCEPTANCE_MATRIX.md` exists | ✅ Verified |
| 2 | `five_surface_inventory_present` | Matrix enumerates `S-BFF`, `S-IAPI`, `S-CLI`, `S-EMRG`, `S-SUPP` | ✅ Verified |
| 3 | `gap06_required_columns_present` | Matrix tables contain surface, canonical object, path type, permissions, degraded behavior, test status, and drill status fields | ✅ Verified |
| 4 | `cli_internal_fallback_repo_truth_restored` | Update root matrix so CLI/internal API rows match implemented coverage in `tools/pantheon_admin/cli.py` and `services/control_plane/internal_api.py` | ⏳ Rework required |
| 5 | `bff_outage_routing_is_correct` | Pause/rollback must fall back to `S-IAPI` / `S-CLI`; `S-EMRG` remains the emergency fast path rather than the generic fallback home | ⏳ Rework required |
| 6 | `runtime_manager_outage_preserves_surface_degradation_truth` | Replace blanket "BFF read unaffected" language with the per-surface degradation model from `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | ⏳ Rework required |
| 7 | `acceptance_evidence_table_uses_repo_true_status` | Replace `CLI not implemented` claims with drill / evidence backlog language that matches actual implementation status | ⏳ Rework required |
| 8 | `non_blocking_backlog_does_not_demote_existing_cli_surface` | Section 9 may defer CLI polish/spec detail, but must not imply the entire CLI fallback surface is unimplemented | ⏳ Rework required |
| 9 | `parent_review_resubmitted_after_repairs` | Parent owner re-handoffs the repaired artifact for Codex review after absorbing the current change request | ⏳ Pending parent repair |
| 10 | `parent_metadata_aligned_for_close` | `ai-status.json` parent entry records the actual artifact path and explicit acceptance list when BG-006 returns for review/finalize | ⏳ Owner-close cleanup required |

---

## 3. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| CLI/internal fallback remains labeled `not implemented` | Production readiness is understated in the wrong way, and drill planning is distorted around nonexistent gaps | Repair the root matrix against the live CLI/internal API code before the next parent handoff |
| Pause/rollback keep routing to `S-EMRG` during BFF outage | Operators may use the wrong control path and blur rollback semantics with kill-switch semantics | Rebuild the routing rows from the degraded operator path and BFF HA policy before review resubmission |
| Runtime-manager outage still claims BFF runtime reads are unaffected | Operators may misread degraded runtime state as healthy instead of seeing explicit unverifiable / unavailable surface behavior | Align the degradation summary to the per-surface rules and "never show false-positive empty state" contract |
| Parent metadata remains incomplete | Review history becomes harder to audit because the artifact and acceptance expectations are not discoverable from the task entry | Update `ai-status.json` through `scripts/ai-status.sh` as part of the parent repair / resubmission cycle |
| Older review-sidecar packet is read without state refresh | Reviewers may assume the parent is still in review rather than reopened for repair | Treat this acceptance packet as the current-state supplement; the earlier review packet remains structurally useful but state-stale |

---

## 4. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| This acceptance packet | `support/sidecars/BG-006/BG-006-SIDECAR-ACCEPTANCE.md` | Support-only dependency map and parent close checklist |
| Earlier review sidecar | `support/sidecars/BG-006/BG-006-SIDECAR-REVIEW.md` | Structural review evidence from the earlier parent-review state |
| Parent artifact under repair | `OPERATOR_ACCEPTANCE_MATRIX.md` | Current BG-006 deliverable draft that still needs repair |
| Current blocking review | `docs/reviews/BG-006-review-codex.md` | Canonical list of reopen issues currently blocking parent closure |
| Earlier approval context | `docs/reviews/BG-006-review-gemini.md` | Earlier review context before the current reopen |
| Repo-true acceptance reference | `docs/02-architecture/consensus/phase2/OPERATOR_ACCEPTANCE_MATRIX.md` | Existing operator acceptance reference showing real CLI/internal fallback coverage |
| Planning task definition | `docs/02-architecture/consensus/phase2/planning-session.json` | Machine-readable BG-006 task materialization |
| GAP-06 rationale | `docs/02-architecture/consensus/phase2/gap-response-matrix.md` | Why BG-006 exists and why it should be packaging-only |
| Degraded-path contract | `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | Current truth for BFF-down fallback and per-surface degradation |
| CLI fallback implementation | `tools/pantheon_admin/cli.py` | Current real admin CLI coverage |
| Internal API fallback implementation | `services/control_plane/internal_api.py` | Current real internal API coverage |

---

## 5. Reviewer Confirmation Note (Qwen)

Qwen review is already recorded in durable task state for this sidecar packet. This packet still does **not** claim that the parent task is complete.

What this packet establishes:

1. `BG-006` is hard-unblocked and structurally close to complete, but it is **not** acceptance-ready in its current durable state.
2. The root matrix already satisfies the GAP-06 shape requirements, so the remaining work is semantic repair rather than missing document scaffolding.
3. The blocking issues are narrow and concrete: repo-true CLI/internal fallback status, BFF-outage routing, runtime-manager outage degradation wording, and parent metadata hygiene.
4. The earlier review sidecar remains useful for structural coverage, but this packet supersedes it for **current parent state** and close readiness.

Reviewer-confirmed next step:

- keep this sidecar in support-only scope
- have the parent owner repair `OPERATOR_ACCEPTANCE_MATRIX.md` and re-submit `BG-006` to Codex
- use Section 2 as the acceptance checklist when deciding whether the repaired parent artifact is truly ready for review / finalization

---

## 6. Codex Owner Finalize Intent

This resumed dispatch is an owner-close pass for the sidecar itself, not a reopening of parent-task content review.

Current sidecar intent:

- provide a durable, support-only current-state packet for `BG-006`
- keep parent blockers explicit and bounded
- close `BG-006-SIDECAR-ACCEPTANCE` as `done` after recording this reviewer-aligned finalize refresh

The parent task remains with its owner. No canonical matrix content, runtime behavior, registry contract, or governance truth was changed while preparing this packet.

---

*Generated by Codex as a sidecar `acceptance_packet` helper for `BG-006`. This file is a support artifact and does not modify canonical truth.*
