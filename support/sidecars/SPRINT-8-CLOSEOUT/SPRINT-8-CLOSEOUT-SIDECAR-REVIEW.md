# SPRINT-8-CLOSEOUT Review Packet and Evidence Summary

**Sidecar Task ID**: `SPRINT-8-CLOSEOUT-SIDECAR-REVIEW`
**Parent Task**: `SPRINT-8-CLOSEOUT`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `review_packet`
**Generated**: 2026-05-18
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
runtime implementation, registry behavior, governance implementation, status
truth, or the reviewed parent closeout packet. The parent owner decides whether
and how to use this packet before finalizing `SPRINT-8-CLOSEOUT`.

This sidecar intentionally did not inspect `current-work.md` or the full
`ai-activity-log.jsonl`. It used the task brief, live task state through
`AI_NAME=Codex2 ./scripts/ai-status.sh show`, parent closeout artifacts, the
Codex review record, and focused repo/GitHub checks.

---

## 1. Current Disposition Snapshot

| Task | Current status | Owner | Reviewer | Evidence |
|---|---:|---|---|---|
| `SPRINT-8-CLOSEOUT` | `review_approved` | `Claude` | `Codex` | `AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT` |
| `SPRINT-8-CLOSEOUT-SIDECAR-REVIEW` | `in_progress` at packet update | `Codex2` | `Claude` | `AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT-SIDECAR-REVIEW` and task brief |
| Parent PR | merged | `task/SPRINT-8-CLOSEOUT` | `dev` | GitHub PR `#124`, merge commit `0ea47b3c0b3e579ceb859de2e554412796a0a784` |
| Parent review record | approved and attached | `Codex` | returns to `Claude` | `support/reviews/SPRINT-8-CLOSEOUT-review-codex.md` |

Parent live status is `review_approved`. Its `next` field says the closeout
artifacts and Sprint 9 candidate packet are accepted and the owner may finalize
`done`. This sidecar is therefore a reviewer handoff aid, not a new gate that
changes the parent task lifecycle.

---

## 2. Scope Boundary and Timing Caveat

The parent closeout artifacts were authored and reviewed as a Sprint 8 snapshot
packet. They correctly satisfy the recorded parent acceptance criteria:

- retrospective with shipped/slipped work and required numeric metrics
- machine-readable EPIC summary with required fields
- Sprint 9 candidate topics with fail-closed broker-live and capital-binding-live reminders
- support/evidence-only changes

There is one coordination caveat for the reviewer:

The live task brief for this sidecar now reports the parent dependencies as
`done`, while the parent closeout packet records the earlier reviewed snapshot
where many Sprint 8 tasks were still `todo` or `review`. This packet does not
rewrite that parent snapshot. If Sprint 8 closeout must represent the latest
post-review task board, Claude and Codex should decide whether a separate delta
note or parent follow-up is needed before parent finalization.

---

## 3. Parent Closeout Evidence

| Artifact | Sidecar review assessment |
|---|---|
| `support/evidence/SPRINT-8-CLOSEOUT/retrospective.md` | Present. Lists shipped/slipped EPIC work, includes `tasks_completed`, `avg_cycle_time_completed_tasks`, and `pass_rate`, and preserves fail-closed invariants. |
| `support/evidence/SPRINT-8-CLOSEOUT/epic_completion_summary.json` | Present and parses as JSON. EPIC entries carry `epic_id`, `status`, `tasks_total`, `tasks_completed`, `tasks_blocked`, and `artifacts_produced`. |
| `support/evidence/SPRINT-8-CLOSEOUT/sprint_9_candidate_topics.md` | Present. Lists five candidate themes and starts with broker-live / capital-binding-live fail-closed reminders. |
| `support/reviews/SPRINT-8-CLOSEOUT-review-codex.md` | Present. Records no blocking findings, focused JSON/schema checks, PR #124 review context, and an approved review decision. Its PR state line was written before the PR merged. |
| GitHub PR `#124` | Merged to `dev`; check rollup showed successful Branch CI Gate and Orchestrator Sync runs. |

The merged PR touched support/review/evidence files only:

- `support/evidence/SPRINT-8-CLOSEOUT/retrospective.md`
- `support/evidence/SPRINT-8-CLOSEOUT/epic_completion_summary.json`
- `support/evidence/SPRINT-8-CLOSEOUT/sprint_9_candidate_topics.md`
- `support/reviews/SPRINT-8-CLOSEOUT-review-codex.md`

---

## 4. Focused Verification

Focused checks run by this sidecar:

```bash
python3 -m json.tool support/evidence/SPRINT-8-CLOSEOUT/epic_completion_summary.json
```

Result: JSON parse passed.

```bash
python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("support/evidence/SPRINT-8-CLOSEOUT/epic_completion_summary.json").read_text())
required = {"epic_id", "status", "tasks_total", "tasks_completed", "tasks_blocked", "artifacts_produced"}
missing = []
for index, epic in enumerate(data.get("epics", []), 1):
    absent = sorted(required - set(epic))
    if absent:
        missing.append((index, epic.get("epic_id"), absent))
print("epics", len(data.get("epics", [])))
print("missing_required", missing)
print("numeric_metrics", data.get("numeric_metrics"))
PY
```

Observed result:

```text
epics 7
missing_required []
numeric_metrics {'tasks_completed': 1, 'tasks_in_review': 1, 'tasks_todo_carry_over': 15, 'avg_cycle_time_days_completed_tasks': 'N/A (only 1 task completed and it was Sprint 7 carry-over)', 'pass_rate': '5.6%', 'epics_closed': 1, 'epics_partial': 1, 'epics_not_started': 5}
```

```bash
gh pr view 124 --json number,state,mergeCommit,statusCheckRollup,url,headRefName,baseRefName
```

Observed result: PR `#124` is `MERGED`, merge commit is
`0ea47b3c0b3e579ceb859de2e554412796a0a784`, and the visible check rollup
reported successful Branch CI Gate and Orchestrator Sync checks. The parent
review file's "PR #124 is open" result is therefore historical, not current.

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT
AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT-SIDECAR-REVIEW
```

Observed result: parent status is `review_approved` with
`support/reviews/SPRINT-8-CLOSEOUT-review-codex.md` attached; this sidecar is
`in_progress` with reviewer `Claude`.

```bash
git diff --check -- support/sidecars/SPRINT-8-CLOSEOUT/SPRINT-8-CLOSEOUT-SIDECAR-REVIEW.md
```

Result: passed with no output.

---

## 5. Non-Claims

This sidecar does not claim:

| Non-claim | Correct disposition |
|---|---|
| The parent closeout packet is a live, continuously updated Sprint 8 board snapshot after subsequent task completions. | Treat the reviewed parent artifacts as the closeout snapshot; decide separately whether a delta note is needed. |
| This packet changes `SPRINT-8-CLOSEOUT` from `review_approved` to `done`. | Only Claude, as parent owner, can finalize per `task-closeout-finalization.md`. |
| This packet changes broker-live, capital-binding-live, canary, or DEP-004 policy. | Fail-closed policy remains exactly as recorded in canonical docs and the parent packet. |
| This packet mutates service code, runtime contracts, governance behavior, or registry behavior. | It only adds support material for the sidecar task. |

---

## 6. Reviewer Checklist for Claude

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime implementation edits? | Yes. It only adds this support packet. |
| Does it accurately state parent live status as `review_approved`, not `done`? | Yes. |
| Does it preserve PR #124 as already merged into `dev`? | Yes. |
| Does it distinguish the approved parent snapshot from newer live dependency completion state? | Yes. |
| Does it avoid asking the reviewer to rewrite parent artifacts inside this sidecar? | Yes. Parent owner/reviewer decide any separate delta. |

---

## 7. Handoff

**To**: `Claude`
**From**: `Codex2`
**Requested review outcome**: Approve this sidecar if the packet is an accurate,
support-only review summary for `SPRINT-8-CLOSEOUT` and the Codex parent review
record.

Recommended reviewer disposition:

1. Approve if the facts above match the intended support record.
2. Request changes only for wording, evidence, or lifecycle mismatches in this
   sidecar packet.
3. If the newer dependency completion state should be reflected before parent
   finalization, route that as a parent closeout/delta decision for Claude and
   Codex rather than as a sidecar canonical change.

---

## 8. Owner Closeout Record

Codex2 performed owner finalization after Claude approved the sidecar review.
The reviewed deliverable remains support-only:

- task-owned packet: this file
- reviewer record: `support/reviews/SPRINT-8-CLOSEOUT-SIDECAR-REVIEW-review-claude.md`
- PR: `#155`, open against `dev` with auto-merge enabled
- branch state: task branch refreshed with `origin/dev` before closeout
- canonical/runtime changes: none

Focused closeout checks:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT-SIDECAR-REVIEW
AI_NAME=Codex2 ./scripts/ai-status.sh show SPRINT-8-CLOSEOUT
gh pr view 155 --json number,state,isDraft,reviewDecision,autoMergeRequest,mergeStateStatus,url,headRefName,baseRefName
gh pr checks 155
git diff --name-status origin/dev...HEAD
git diff --check origin/dev...HEAD -- support/sidecars/SPRINT-8-CLOSEOUT/SPRINT-8-CLOSEOUT-SIDECAR-REVIEW.md support/reviews/SPRINT-8-CLOSEOUT-SIDECAR-REVIEW-review-claude.md
```

Closeout observations:

- sidecar live state is `review_approved`, owner `Codex2`, reviewer `Claude`
- Claude review file is attached to the sidecar status record
- visible PR checks were passing before this final closeout update
- PR diff remains scoped to support sidecar/review material
- parent `SPRINT-8-CLOSEOUT` live lifecycle is separate and must be finalized by
  its owner/reviewer; this sidecar does not move the parent task
