# OPENCLAW-CRON-WRITE-SCOPE Sidecar Acceptance Follow-up 4

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-4`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-07-04

> Scope constraint: this is support material only. It does not edit canonical
> truth, L1 policy, runtime contracts, router/governance implementation, the
> OpenClaw gateway adapter, cron registrar code, or supervisor cadence. The
> parent owner decides whether this packet is absorbed into
> `OPENCLAW-CRON-WRITE-SCOPE` closeout.

---

## 1. Headline: No Material Change Since Follow-up 3

Follow-up 3
(`support/sidecars/OPENCLAW-CRON-WRITE-SCOPE/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`)
closed `done` at `2026-07-04T06:04:08Z` (PR #2903 merged into `dev`). This
follow-up was auto-dispatched roughly **53 minutes later**
(`2026-07-04T06:57:00Z`) by the same `supervisor-underutilization` mechanism
that created Follow-ups 2 and 3.

Checked against the canonical status root (`$PANTHEON_STATUS_ROOT/ai-status.json`,
per [[reference_pantheon_status_root]]) and `ai-activity-log.jsonl`:

| Fact checked | Follow-up 3 read (06:04Z) | This read (07:0xZ) | Changed? |
|---|---|---|---|
| Parent `status` | `blocked` | `blocked` | No |
| Parent `waiting_for` | `Human/Ops` | `Human/Ops` | No |
| Parent `last_update` | `2026-07-03T14:11:35Z` | `2026-07-03T14:11:35Z` | No — still not refreshed to reflect the PR #2837 merge |
| Evidence of live `openclaw-approve-adapter-cron-scope.sh` / `openclaw-cron-write-scope-smoke.sh` execution | none found | none found (re-checked `ai-activity-log.jsonl` after Follow-up 3's closeout) | No |
| Focused test suite (`test_cron.py` + `test_main.py` + `test_persona_cron_registrar.py`) | 40 passed | 40 passed | No |
| PR #2837 merge state | `MERGED` into `dev`, ancestor confirmed | `MERGED` into `dev`, ancestor confirmed | No |

Nothing in the parent's blocking condition — the Human/Ops privileged adapter
device scope grant — has moved. Re-running the same acceptance checklist and
dependency map from Follow-up 3 would only restate it verbatim, so this
packet does not duplicate that table; see Follow-up 3 §4 for the governing
per-item disposition (all still accurate).

---

## 2. New Observation: Zombie Task Branch

One thing not previously flagged: `origin/task/OPENCLAW-CRON-WRITE-SCOPE`
still exists on GitHub even though its PR (#2837) merged at
`2026-07-04T05:36:51Z`.

```bash
git fetch origin dev
git branch -r | grep OPENCLAW-CRON-WRITE-SCOPE
#   origin/task/OPENCLAW-CRON-WRITE-SCOPE
#   origin/task/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE
#   origin/task/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-2
#   origin/task/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-3

git rev-list --count origin/dev..origin/task/OPENCLAW-CRON-WRITE-SCOPE
# 0
git merge-base --is-ancestor origin/task/OPENCLAW-CRON-WRITE-SCOPE origin/dev && echo ancestor
# ancestor
```

The branch is fully merged (0 commits ahead, confirmed ancestor of `dev`) but
was not auto-deleted by GitHub after merge. Per
`.orchestrator/skills/task-closeout-finalization.md` § "Chair Man Oversight",
this is exactly the "`task/<id>` branches that exist on origin without a
corresponding open PR (zombie task branch — recommend deletion)" case. This
packet does not delete the branch itself (that is a repo-hygiene action
outside an `acceptance_packet` sidecar's scope), but flags it for
chair-review or the parent owner.

---

## 3. Recommendation: Throttle Further Acceptance-Packet Sidecars For This Parent

This is now the **fourth** `acceptance_packet` sidecar dispatched against the
same parent (`ACCEPTANCE`, `FOLLOWUP-2`, `FOLLOWUP-3`, this one) inside about
36 hours, and the third one to close within roughly 2 hours of the last one.
Each has confirmed the identical blocker: the parent cannot progress without
a human/operator running the privileged
`openclaw devices approve <requestId>`-equivalent grant
(`scripts/openclaw-approve-adapter-cron-scope.sh`), which the harness's own
safety classifier correctly refuses to let an agent infer/self-grant.

Concretely, nothing an `acceptance_packet` sidecar can discover or verify
will change until one of these happens:

- Human/Ops runs `bash scripts/openclaw-approve-adapter-cron-scope.sh` against
  the live gateway, or
- the parent's own `ai-status.json` entry changes (new `owner`, new
  `waiting_for`, or a status transition), or
- the branch/PR state around `OPENCLAW-CRON-WRITE-SCOPE` changes again.

Recommendation for chair-review / the supervisor's underutilization dispatch
policy: do not auto-create another `acceptance_packet` sidecar for
`OPENCLAW-CRON-WRITE-SCOPE` until one of the three trigger conditions above is
observed. A fifth packet re-confirming the same `blocked` / `waiting_for:
Human/Ops` state would not add verification value and would just consume a
worker cycle that could go to genuinely unblocked work.

---

## 4. Verification Evidence (read-only, no source files touched)

```bash
PYTHONPATH="$PWD/services/control-plane/cron:$PWD/services/control-plane/router" \
  python3 -m pytest \
    services/control-plane/cron/test_cron.py \
    services/control-plane/router/test_main.py \
    services/control-plane/cron/test_persona_cron_registrar.py -q
# 40 passed in 7.27s
```

No canonical, runtime, registry, governance, router, or cron implementation
file was modified by this sidecar.

---

## 5. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is complete | Parent owner, after live proof |
| That Human/Ops has performed the scope-approval grant | Human/Ops |
| That the parent's acceptance criteria 1-3 have been demonstrated live | Parent owner, after the grant and smoke run |
| That the zombie `origin/task/OPENCLAW-CRON-WRITE-SCOPE` branch should be deleted by this sidecar | Chair-review / repo hygiene owner |
| That this packet supersedes Follow-up, Follow-up 2, or Follow-up 3 | All three remain accurate historical record; this packet only confirms no change and adds the branch-hygiene observation and dispatch-throttling recommendation |

---

## 6. Handoff

**To**: `Claude`
**From**: `Claude2`
**Requested review outcome**: Approve this sidecar if it accurately reflects
that no material change has occurred since Follow-up 3, that the zombie
branch observation is correct, and that the recommendation to throttle
further identical sidecars for this parent is reasonable.

Recommended reviewer checks:

1. Confirm the parent's `status`/`waiting_for`/`last_update` fields in the
   live status root match this packet's §1 table.
2. Confirm `origin/task/OPENCLAW-CRON-WRITE-SCOPE` is indeed merged-but-not-deleted
   (§2).
3. Consider whether §3's dispatch-throttling recommendation should be acted
   on by chair-review or the supervisor's sidecar-creation policy.
