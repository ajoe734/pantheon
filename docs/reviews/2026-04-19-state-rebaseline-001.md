# 2026-04-19 STATE-REBASE-001 Rebaseline Record

Record layer document for `STATE-REBASE-001`.
This note captures the concrete tracking drift that was rebaselined on
2026-04-19 and the resolution applied to each layer.

## Scope

Reconcile:

- `ai-status.json`
- `current-work.md`
- `WORKBENCH_DELIVERY_BACKLOG.md`
- `.orchestrator/state.json`

so they describe one truthful execution picture for reviewed operator and
governance closeout work versus the real remaining backlog.

## Drift Findings

### 1. Idle agent summaries were retaining stale dispatch text

`ai-status.json` had the correct active task ownership, but idle agents could
still carry `next` messages copied from older auto-dispatch events. That made
`current-work.md` look like multiple stale execution slices were still active
when they were not.

### 2. Workbench backlog still treated reviewed operator and governance loops as active product backlog

`WORKBENCH_DELIVERY_BACKLOG.md` still listed:

- `GV-02` Approval Queue
- `GV-04` Deployment Diff
- `OC-01` to `OC-05`

as remaining backlog with "frontend loop not started" or similar wording, even
though the current coordination summary shows those surfaces at
`frontend_feedback_reviewed` with Pantheon review packets approving closeout.

### 3. Closeout bookkeeping and remaining module backlog were mixed together

The real residual work for those reviewed surfaces is canonical closure-record
sync, not missing product implementation. Keeping them in the remaining module
backlog made the backlog look larger and less truthful than the actual
execution picture.

## Resolutions

1. Updated `scripts/ai_status.py` so `recompute_agents()` clears stale `next`
   text when an agent is idle and has no queued work. Re-rendered derived state
   will now show no active assignment instead of an unrelated old dispatch.
2. Updated `WORKBENCH_DELIVERY_BACKLOG.md` to move reviewed governance and
   Operator Console Wave 2 surfaces out of the remaining backlog and into the
   landed baseline list.
3. Added an explicit rule to the workbench backlog document: closure-record
   sync belongs in `ai-status.json` closeout tasks and does not, by itself,
   keep a module on the remaining product backlog.
4. Kept the residual closeout work materialized as `APP-003-CLOSEOUT-001`
   instead of pretending those surfaces are still unfinished module delivery.

## Resulting Truth

- `ai-status.json` remains the canonical task board for active execution.
- `current-work.md` is regenerated from that task board without stale idle-agent
  dispatch messages.
- `WORKBENCH_DELIVERY_BACKLOG.md` now focuses on genuinely unfinished module
  backlog rather than reviewed loop-closeout bookkeeping.
- `.orchestrator/state.json` task state for `STATE-REBASE-001` already matched
  the canonical `in_progress` execution truth after sync; no manual correction
  was required there.
