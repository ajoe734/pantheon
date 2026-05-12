# SVC-RENAME-001 Sidecar Review Packet

**Sidecar Task:** SVC-RENAME-001-SIDECAR-REVIEW
**Author:** Claude (governance-review lane)
**Date:** 2026-05-12
**Parent Task:** SVC-RENAME-001 — Inventory services/ duplicate dirs and produce migration map
**Parent Status:** review_approved
**Reviewer of this packet:** Codex2

---

## Purpose

This packet summarises the evidence trail for SVC-RENAME-001 and maps each acceptance criterion to the delivered artifact, supporting Codex2's closeout review and audit trail.
This is a support-only artifact. No canonical truth files were modified.

**Reviewer clarification (2026-05-12):** The packet was prepared while the parent closeout path was still being handed off. In the current task state, `SVC-RENAME-001` is no longer active in `ai-status.json`, and the task archive records terminal `done`. Treat the closeout checklist below as historical validation evidence, not an instruction to re-run parent finalization.

---

## Parent Task At a Glance

| Field | Value |
|---|---|
| Owner | Codex2 |
| Final reviewer | Codex |
| Primary artifact | `docs/architecture/services-namespace-migration-map-2026-05-10.md` |
| Task-scoped commit | `e2a9d80c` |
| Commit contains | migration map + two review records only; no application code |
| Current status | Archived terminal `done` in the current task state; this packet remains support evidence |

---

## Review Chain

| Round | Reviewer | Date | Disposition |
|---|---|---|---|
| 1 | Codex2 | 2026-05-10 | Changes requested — 3 blocking findings |
| 2 | Claude2 | 2026-05-10 | Approved — all 3 findings resolved |
| 3 (final) | Codex | 2026-05-12 | Approved — confirmed clean plan-only delivery |

### Codex2 Blocking Findings (Round 1)

All three were resolved before the Claude2 approval.

1. **Pair E missing downstream consumer** — `services/telemetry/feedback_adapter.py` `sys.path` injection not documented; shim not specified. → **Resolved:** Pair E now includes the feedback adapter import site, rewrite target (`services.trader_feedback.store`), shim package entries, and a High-severity risk row.

2. **Pair J incorrect claim** — Plan stated `services/research/trl` exists when it does not. → **Resolved:** Map now explicitly states "No `services/research/trl` directory exists today" and describes the move as a new target creation.

3. **Pair A non-actionable shim layout** — `services/control-plane/internal` is not Python-importable (hyphen). → **Resolved:** Six-step zero-downtime shim laid out in Sections 1 and 4: keep `services/control_plane/` as the importable namespace, add `services/control_plane/internal/` loader modules using `importlib.util.spec_from_file_location`.

---

## Acceptance Criteria Evidence Map

| Criterion | Evidence | Status |
|---|---|---|
| Inventory of all duplicate-looking dirs with role classification | Sections 1 (Pairs A–J): 10 pairs, each with property table and explicit role classification (true duplicate / role-separated / snake-kebab split) | ✅ Pass |
| Grep/import summary of all import sites referencing to-be-moved paths | Per-pair import site tables with file path and line number for Pairs A, B, E, F, G, H, I, J; runtime/path references for Pair J scripts | ✅ Pass |
| Migration map: file/destination/import-rewrite rules | Sections 3 and 4 cover Pairs A, B, E, J with per-file source→destination and old→new import columns | ✅ Pass |
| Risk table covering docker-compose service refs and downstream consumers | Section 5 (compose changes per pair) and Section 6 (risk table with severity, affected pairs, mitigations) | ✅ Pass |
| Roll-forward plan designed to preserve tests via shims and phasing | Section 7 defines SVC-RENAME-002 through SVC-RENAME-005 execution order with compat shim requirements; Pair A compat shim approach is explicit | ✅ Pass |
| No code changes in this task | Commit `e2a9d80c` contains only the migration map and review records; Codex review (2026-05-12) verified via `git show --stat --oneline --name-status e2a9d80c` | ✅ Pass |

---

## Scope Summary by Pair

| Pair | Directories | Classification | Action Required |
|---|---|---|---|
| A | `control_plane/` (snake) vs `control-plane/` (kebab) | True duplicate name conflict | P1 — SVC-RENAME-003 |
| B | `registry/` vs `registry-core/decision-domain/` | Role-separated (service vs schema library) | P2 — SVC-RENAME-004 |
| C | `incident/` vs `incidents/` | Role-separated (library vs HTTP service) | P3 — README only |
| D | `source_ingestion/` vs `source-ingest` Docker | Intentional snake/kebab split | P3 — README only |
| E | `feedback/` vs `control-plane/feedback/` | True collision + split Dockerfile | P1 — SVC-RENAME-002 |
| F | `governance/` vs `control-plane/governance/` | Role-separated (HTTP vs domain library) | P3 — defer |
| G | `lineage-read/` vs `telemetry/lineage_read/` | Role-separated and clean | No action |
| H | `promotion/` vs `registry/promotion/` | Role-separated (HTTP vs domain library) | No action |
| I | `runtime-manager/` vs `execution/runtime-manager/` | Role-separated (HTTP vs domain library) | No action |
| J | `learning/` vs `research/` | True functional overlap | P2 — SVC-RENAME-005 |

---

## Critical Path Observations for Closeout

The following are observations for Codex2 to consider during the `review_approved → done` closeout. None of these block closeout — SVC-RENAME-001 is plan-only.

### 1. Pair A implementation files already present in HEAD

Codex's review (2026-05-12) notes: "Current HEAD already contains later Pair A migration files in `services/control-plane/internal/` and `services/control_plane/internal/` from a subsequent commit." This is consistent with the plan document stating "Plan only — no code changed" for task-scoped commit `e2a9d80c`. The subsequent commit is not part of SVC-RENAME-001's delivery scope.

### 2. docker-compose.control.yml feedback build not converged

The map documents that `docker-compose.yml` and `docker-compose.control.yml` build `feedback:` from different Dockerfiles (risk table, High, Pair E). After SVC-RENAME-002 executes the Pair E rename, both compose files must be re-checked. This is an execution concern for SVC-RENAME-002, not a gap in the SVC-RENAME-001 plan.

### 3. `scripts/validate_bg003.py` likely not in CI

Risk table (Low, Pair B) notes this script may not be exercised by CI. SVC-RENAME-004 (Pair B) should add a smoke-test assertion before removing the old `registry-core` path.

### 4. SVC-RENAME-005 (Pair J) scope is large

The TRL import/path rewrites span five scripts, two test files, the research-worker-gateway entrypoint, and activation-gate metadata in two services. This is within the plan scope but should be sized as a full implementation task, not a small cleanup.

---

## Task-Scoped Commit Verification

Codex (final reviewer, 2026-05-12) confirmed:

```bash
git merge-base --is-ancestor e2a9d80c HEAD  # exit 0 — ancestor confirmed
git show --stat --oneline --name-status e2a9d80c
# Shows: docs/architecture/services-namespace-migration-map-2026-05-10.md
#         docs/reviews/2026-05-10-svc-rename-001-codex2-review.md
#         docs/reviews/2026-05-10-svc-rename-001-claude2-review.md
# No application code files changed.

git diff --check HEAD -- docs/architecture/services-namespace-migration-map-2026-05-10.md \
    docs/reviews/2026-05-10-svc-rename-001-codex2-review.md \
    docs/reviews/2026-05-10-svc-rename-001-claude2-review.md
# Clean — no trailing whitespace or merge conflict markers.
```

---

## Reviewer Handoff Checklist (for Codex2)

This checklist supported the `review_approved → done` closeout per `.orchestrator/skills/task-closeout-finalization.md`; after parent archival, use it only as a verification record.

- [ ] Re-read `docs/architecture/services-namespace-migration-map-2026-05-10.md` and confirm it matches the approved state
- [ ] Confirm task-scoped commit `e2a9d80c` is still an ancestor of HEAD (`git merge-base --is-ancestor e2a9d80c HEAD`)
- [ ] Confirm HEAD in the current worktree does not add application code to SVC-RENAME-001's claimed scope
- [ ] Review `docs/reviews/2026-05-12-svc-rename-001-codex-review.md` (final Codex approval) for any conditional notes
- [ ] Run `git status --short` and isolate any task-owned dirty files from unrelated changes before committing
- [ ] Create a task-scoped closeout commit (subject includes `SVC-RENAME-001`, body includes `LLM-Agent: Codex2`, `Task-ID: SVC-RENAME-001`, `Reviewer: Codex`)
- [ ] Run `AI_NAME=Codex2 ./scripts/ai-status.sh done SVC-RENAME-001 "<checkpoint message>"`
- [ ] Push to configured upstream after done transition

---

## Files Referenced

| File | Role |
|---|---|
| `docs/architecture/services-namespace-migration-map-2026-05-10.md` | Primary task artifact |
| `docs/reviews/2026-05-10-svc-rename-001-codex2-review.md` | Round 1 review (changes requested) |
| `docs/reviews/2026-05-10-svc-rename-001-claude2-review.md` | Round 2 review (approved) |
| `docs/reviews/2026-05-12-svc-rename-001-codex-review.md` | Round 3 final review (approved) |
| `ai-status.json` (task `SVC-RENAME-001`) | Durable task state |
| `.orchestrator/task-briefs/svc_rename_001_sidecar_review.md` | Sidecar task context |

---

*Sidecar packet prepared by Claude (governance-review lane). No canonical truth files were modified. Handoff to Codex2 for review and use during SVC-RENAME-001 closeout.*
