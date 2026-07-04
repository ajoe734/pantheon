# OPENCLAW-PERSONA-CRON-BACKFILL Acceptance Follow-up 5

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-4` (this file is the
FOLLOWUP-5 content requested by that task's reopen verdict; no new sidecar
task id was created for it — see `Task Identity Note` below)
**Helper parent:** `OPENCLAW-PERSONA-CRON-BACKFILL`
**Prepared by:** `Claude`
**Reviewer:** `Claude2`
**Date:** `2026-07-04`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet captures the parent's
> current `status: review` evidence bundle (68/68, orphan-job note, and the
> registered=0 bug-fix-vs-new-registration nuance) that FOLLOWUP-4's Reviewer
> Note (commit `3fcc6961c`) required before `Codex` reviews the parent. It
> does not modify L1 canonical truth, OpenClaw runtime contracts, BFF/runtime
> implementation, persona registry behavior, governance behavior, supervisor
> dispatch policy, or the four prior sidecar packets.

## Task Identity Note

FOLLOWUP-4's reopen verdict said "reopening to Claude to author FOLLOWUP-5."
The dispatch that produced this file is still tracked under task id
`OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-4` (status
`in_progress`, owner `Claude`, reviewer `Claude2` — confirmed via
`AI_NAME=Claude python3 scripts/ai_status.py show
OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-4` at the time of
writing). No `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-5`
task id exists in the canonical status root or archive (confirmed:
`ai_status.py show` for that id returns `Unknown task`). Rather than invent a
task id outside this dispatch's scope, this content is delivered as a
FOLLOWUP-5-numbered file under the existing FOLLOWUP-4 task, matching the
sequential naming the reviewer asked for while keeping task lifecycle
ownership on the task id this worker was actually dispatched with.

## Current Parent Read

`AI_NAME=Claude python3 scripts/ai_status.py show OPENCLAW-PERSONA-CRON-BACKFILL`
reports the parent unchanged from what FOLLOWUP-4's Reviewer Note recorded:
`status: review`, owner `Claude`, reviewer `Codex`, `last_update:
2026-07-04T16:05:17Z`. Full `next` evidence bundle, re-confirmed:

| Parent fact | Acceptance interpretation |
|---|---|
| `cron.list` total `72` = `68` real jobs covering all 17 existing personas (0 missing) + 4 pre-existing orphan jobs for a non-existent test persona `persona-diag-local-4`. | The 17-persona × 4-workflow acceptance target (`68`) is met. The 4 orphan jobs are explicitly out of scope for this task's acceptance — see `Orphan-Job Note` below. |
| Idempotent reconcile reran twice after fixing 2 registrar bugs, both times `registered=0 skipped=68 failed=0`. | Reruns are idempotent. See `Bug-Fix-vs-New-Registration Nuance` below for why `registered=0` here does not mean no transport was exercised for the earlier `67→68` delta — it means the delta traced to bug fixes, not a new registration action. |
| Force-run confirmed `cron.runs status ok` for two distinct personas (`persona-tw-equity`, `persona-crypto`). | Satisfies the "force-run spans personas" checklist item. |
| `sessionTarget: main` normalization finding is pointed at `OPENCLAW-OODA-PACKET-CLOSURE`, not claimed as newly resolved. | Correctly scoped; unchanged from FOLLOWUP-2/3/4. |
| PR #2985 (`task/OPENCLAW-PERSONA-CRON-BACKFILL` → `dev`), commit `05f51918e`, auto-merge enabled. | **Not yet merged as of this packet.** Independently checked via `gh pr view 2985`: `state: OPEN`, `mergeStateStatus: BEHIND`, all status checks (`Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, `Forward to orchestrator`) `SUCCESS`, `autoMergeRequest` enabled at `2026-07-04T16:04:00Z`. `git merge-base --is-ancestor 05f51918e origin/dev` confirms `05f51918e` is not yet an ancestor of `origin/dev` (fetched tip `8556643b8`). This is a normal in-flight auto-merge state (`BEHIND` just means the base branch advanced since the PR branch was cut), not a failure — but the parent should not be treated as merged into `dev` yet. |
| `services/control-plane/cron/` suite reports `39 passed` per the parent's own evidence. | This sidecar independently reran the narrower registrar suite (see `Verification Run` below) rather than the full directory; both are green. |

## Orphan-Job Note

The parent's evidence records 4 pre-existing cron jobs for `persona-diag-local-4`,
a test persona id that is not in the current 17-persona agent list. The
parent's own text attributes these as likely residue from
`OPENCLAW-CRON-WRITE-SCOPE`'s own live verification pass (a different, already
`done` task), not from this task's backfill work. The parent attempted removal
but was blocked by the harness permission classifier because the jobs
pre-date this session and were not created by it.

Acceptance interpretation:

- These 4 jobs are **not** part of this task's 17-persona / 68-job acceptance
  target and do not indicate a backfill gap — the count that matters
  (`68` real jobs for the 17 real personas) is fully accounted for.
- Cleanup of the orphan jobs is legitimate follow-up work but should not
  block approving the parent. The parent's own recommendation — a
  human/ops-authorized `cron.remove` for those 4 ids, or a small separate
  task — is reasonable and this packet endorses it as a non-blocking
  suggestion, not a new acceptance requirement.

## Bug-Fix-vs-New-Registration Nuance (for `Codex`, the parent's reviewer)

This is the specific nuance FOLLOWUP-4's Reviewer Note asked the next
follow-up to make explicit.

The parent's evidence shows `registered=0` on both post-fix reconcile reruns
— meaning **no job was newly created** during this evidence-gathering pass.
The move from the previously-recorded `67/68` (FOLLOWUP-2/3) to the current
`68/68` traces to two registrar bugs the owner found and fixed while
investigating, not to a new adapter-proxy or docker-exec registration action:

1. A `job_name` truncation collision (two distinct jobs were colliding onto
   the same truncated name).
2. A `cron.list` `limit=500` silent-swallow that had been hiding an
   already-existing 68th job from prior counts.

Read plainly: **the 68th job already existed on the gateway before this
evidence pass began** — it was undercounted by the two bugs above, not
missing. Once the registrar code was fixed, the existing job became visible
in `cron.list`, and the reconcile correctly reported `skipped` (not
`registered`) for it, because it already existed.

Why this matters for the "transport labeling is precise" checklist item
(carried forward from FOLLOWUP-2/3/4): that item exists to prevent
docker-exec evidence and adapter-proxy evidence from being conflated. This
delta involves **neither** transport — it is a counting/visibility bug fix,
not a registration event. `Codex` should not read the `67→68` change as new
evidence that either transport path was exercised for a previously-missing
job; no such job was missing.

### Evidence-quality note: live-state self-correction during diagnosis

The parent's evidence also records that, while diagnosing the `limit=500`
bug, the owner twice accidentally created real duplicate jobs on the live
gateway (46, then 36 — presumably from repeated reconcile invocations before
the truncation bug was caught) before catching it. Both times this was
identified via job-id-set diffing against a pre-change snapshot and the
duplicates were removed; the parent's evidence states the final gateway
state was verified to match the original 72-job snapshot exactly (same ids)
before the idempotent reruns that report `registered=0 skipped=68 failed=0`.

This packet does not doubt this account, but flags it as a good target for
`Codex` to independently spot-check (e.g., pull a fresh `cron.list` job-id
set and diff it against the id set recorded at parent-evidence time) given
that it describes multiple live-mutation-and-cleanup cycles against a shared
gateway rather than a single clean read. This is a suggested verification
step, not a new acceptance requirement — the checklist item "Backfill was
live, not dry-run" and "Rerun is idempotent" already cover the underlying
concern.

## Acceptance Checklist Status (carried forward, evaluated against current evidence)

The checklist itself is unchanged from FOLLOWUP-2/3/4. This section adds a
status read against the parent's current evidence — informational for the
reviewer, not a relaxation or addition to the bar.

| Check | Required evidence | Current read |
|---|---|---|
| Persona inventory is explicit | Persona ids discovered/backfilled listed or parseable. | Parent evidence references "the 17 existing personas"; count is explicit. |
| Final expected count is closed | `cron.list`/verifier shows `68/68`, or names exact skip. | **Met**: `72` total = `68` real (17×4) + 4 named, explained orphan jobs. |
| Four workflow jobs per persona | `pantheon.ingest/review/retrain/deploy` per persona. | Implied by the `68 = 17×4` accounting; not independently re-verified by this sidecar. |
| Canonical schedules preserved | `WORKFLOW_CATALOG` schedules used. | Not independently re-verified by this sidecar; carried forward from prior packets as unchanged. |
| Backfill was live, not dry-run | `failed=0`, `dry_run_personas=0`, or live command evidence. | Parent evidence shows `failed=0` on reruns; live gateway mutation is further evidenced by the accidental-duplicate-and-cleanup episode above. |
| Rerun is idempotent | Second run shows no duplicates, all skipped. | **Met**: two reruns after bug fixes, both `registered=0 skipped=68 failed=0`. |
| `sessionTarget` interpreted correctly | Proof uses submitted payload/metadata, not persisted field. | Parent correctly points this at `OPENCLAW-OODA-PACKET-CLOSURE`, not claimed as resolved here. |
| Force-run evidence spans personas | ≥2 personas, `cron.runs status ok`. | **Met**: `persona-tw-equity`, `persona-crypto`. |
| Creation-time path not overclaimed | New `POST /bff/personas` claims separated from backfill claims. | Parent does not claim new creation-time registration in this evidence; not applicable here. |
| Paper-only boundary holds | No live capital/broker/canary effects. | No such claims present in parent evidence. |
| Downstream OODA routing separated | Points to `OPENCLAW-OODA-PACKET-CLOSURE`, not claimed solved. | Held — see `sessionTarget` row above. |
| Transport labeling is precise | Adapter-proxy vs docker-exec kept distinct; no retroactive relabeling. | Held, and clarified further by the `Bug-Fix-vs-New-Registration Nuance` above: the final `67→68` delta used **neither** transport — it was a counting bug fix. |

This packet does not mark the parent as fully accepted — that determination
belongs to `Codex` as the parent's reviewer, and to whether PR #2985 actually
merges into `dev`.

## Dependency Map Update (unchanged from FOLLOWUP-4)

| Dependency | Current acceptance impact |
|---|---|
| `OPENCLAW-CRON-WRITE-SCOPE` | Confirmed unchanged: archived, `terminal_status: done`, `terminal_outcome: completed`, merge commit `0e6d3761b`. |
| `OPENCLAW-OODA-PACKET-CLOSURE` | Confirmed unchanged: owner `Claude2`, reviewer `Codex`, `status: todo`, `last_update: 2026-07-04T12:32:15Z`. Depends on this parent; no new pressure. |
| PR #2985 merge state | New in this packet: `OPEN`, `mergeStateStatus: BEHIND`, all checks green, auto-merge enabled, not yet an ancestor of `origin/dev`. Reviewer should recheck merge status close to review time since `BEHIND` PRs update automatically once GitHub re-merges the base. |

## Suggested Evidence Commands

```bash
# Re-check parent status against the canonical status root.
AI_NAME=Claude python3 scripts/ai_status.py show OPENCLAW-PERSONA-CRON-BACKFILL

# Re-check PR #2985 merge state.
gh pr view 2985 --repo ajoe734/pantheon --json state,mergeStateStatus,autoMergeRequest,statusCheckRollup

# Re-check whether the parent's evidence commit has landed in dev.
git fetch origin dev --quiet
git merge-base --is-ancestor 05f51918e origin/dev && echo "merged" || echo "not yet merged"
```

## Verification Run For This Packet

`python3 -m pytest services/control-plane/cron/test_persona_cron_registrar.py -q`
reported `19 passed in 1.29s` in this worktree, confirming the cron registrar
contract this packet references is unchanged and green.

## Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| The parent is complete / approved | `Codex` (parent reviewer) after reviewing this evidence bundle |
| PR #2985 has merged into `dev` | GitHub merge state; independently confirmed still `OPEN`/`BEHIND` at packet time |
| The 4 orphan `persona-diag-local-4` jobs are part of this task's scope | Explicitly out of scope; cleanup is separate follow-up work |
| The accidental live-gateway duplicate-creation episode was independently re-verified by this sidecar | Flagged as a suggested spot-check for `Codex`, not independently reproduced here |
| Persisted `sessionTarget: main` is a canonical architecture decision | Parent evidence plus `OPENCLAW-OODA-PACKET-CLOSURE` |
| OODA loop turns, evolution programs, or broker-side actions are live | Separate runtime/readback evidence |
| Any L1 policy, runtime contract, registry, governance, or broker behavior changed | Out of scope for this sidecar |

## Handoff

**To:** `Claude2`
**From:** `Claude`
**Requested review outcome:** Approve this sidecar if it accurately captures
the parent's current `review`-status evidence bundle, the orphan-job
scoping, and the bug-fix-vs-new-registration nuance for `Codex`, without
inventing new acceptance requirements or overclaiming PR #2985's merge state.

Recommended reviewer focus:

1. Confirm the parent's `status: review` / `68/68` evidence bundle (orphan
   jobs, idempotent reruns, force-run, `sessionTarget` scoping) still reads
   the same in the canonical status root at review time.
2. Confirm PR #2985's merge state independently — it may have merged into
   `dev` by the time this is reviewed, which would be a positive update, not
   a packet error.
3. Confirm the `Bug-Fix-vs-New-Registration Nuance` section correctly
   represents that the `67→68` delta involved no new transport action, so it
   is ready for `Codex` to read without misinterpreting the transport
   checklist item.
4. Confirm this packet does not broaden into canonical/runtime changes or
   relax/add to the FOLLOWUP-2/3/4 acceptance checklist.
