# OPENCLAW-CRON-WRITE-SCOPE Sidecar Acceptance Follow-up 5

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-5`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-07-04

> Scope constraint: this is support material only. It does not edit canonical
> truth, L1 policy, runtime contracts, router/governance implementation, the
> OpenClaw gateway adapter, cron registrar code, supervisor cadence, or the
> parent implementation branch. The parent owner decides whether this packet is
> absorbed into `OPENCLAW-CRON-WRITE-SCOPE` closeout.

---

## 1. What Changed Since Follow-up 4

Follow-up 4 recorded the parent as `blocked` on `Human/Ops` and recommended
throttling further acceptance-packet sidecars until the parent state changed.
The parent state has now changed, so this fifth packet is not just another
duplicate blocker confirmation.

Current live status root reads:

| Field | Follow-up 4 read | Current read |
|---|---|---|
| Parent status | `blocked` | `in_progress` |
| Parent owner / reviewer | `Claude` / `Codex` | `Claude` / `Codex` |
| Parent `next` | Human/Ops privileged grant pending | `Supervisor re-dispatched OPENCLAW-CRON-WRITE-SCOPE; task remains in progress.` |
| Parent `last_update` | `2026-07-03T14:11:35Z` | `2026-07-04T13:37:17Z` |
| Current parent PR | none active in Follow-up 4's current read | PR #2962 open: `OPENCLAW-CRON-WRITE-SCOPE: fix live persona cron registration path` |

The former "zombie branch" observation from Follow-up 4 has also changed:
`origin/task/OPENCLAW-CRON-WRITE-SCOPE` is no longer merely a merged stale
branch. It now backs open PR #2962 and contains new parent implementation
commits.

Commands used:

```bash
AI_NAME=Codex python3 scripts/ai_status.py show OPENCLAW-CRON-WRITE-SCOPE
AI_NAME=Codex python3 scripts/ai_status.py show OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-5
gh pr view 2962 --json number,state,url,title,mergeStateStatus,statusCheckRollup,commits,files
```

---

## 2. Current Parent PR Read

PR #2962 is open against `dev`:

| Field | Current read |
|---|---|
| PR | `https://github.com/ajoe734/pantheon/pull/2962` |
| State | `OPEN` |
| Head / base | `task/OPENCLAW-CRON-WRITE-SCOPE` -> `dev` |
| GitHub checks | Branch CI Gate check runs are green at the latest queried head |
| Merge state | `BEHIND` |
| PR file list | 4 files: `scripts/openclaw-cron-write-scope-smoke.sh`, `services/control-plane/bff/main.py`, `services/control-plane/cron/persona_cron_registrar.py`, `services/control-plane/cron/test_persona_cron_registrar.py` |

Important caveat: after fetching the latest `origin/dev`, the local comparison
`git diff --name-status origin/dev..origin/task/OPENCLAW-CRON-WRITE-SCOPE`
also shows unrelated deletion noise from files that landed on newer `dev` after
the parent branch refresh:

```text
D .orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_5.md
D .orchestrator/task-briefs/devloop_paper_binding_restore_001_closeout_evidence.md
D support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
```

That is consistent with GitHub reporting `mergeStateStatus: BEHIND`. Parent
closeout should not merge PR #2962 until the branch is refreshed against the
current `dev` and the diff is reduced back to the intended parent scope.

---

## 3. Live Evidence Movement

The activity log and PR body show real movement after Follow-up 4:

1. PR #2962's first implementation commit claims the former Human/Ops gate was
   cleared: adapter `cron.add` via proxy worked, the BFF persona-create path
   registered four OODA cron jobs, and adapter scope survived container
   recreate with volumes preserved.
2. Codex reviewed/reopened the parent at `2026-07-04T13:24:47Z` because the
   live smoke script itself was not reliable: adapter `cron.add/list/remove`
   worked with a longer timeout, but `scripts/openclaw-cron-write-scope-smoke.sh`
   timed out at 30 seconds during `cron.add` and left an orphan job until manual
   cleanup.
3. Claude then committed `ce39fdcfe1de68de36f5dec71bb45ec41c9aef53`, changing
   only `scripts/openclaw-cron-write-scope-smoke.sh`. The commit raises
   `curl` timeouts, adds cleanup-by-name when `JOB_ID` was never captured, and
   says the live smoke passed end to end with no remaining orphan smoke jobs.

This sidecar did not rerun live gateway or BFF operations. It records the
parent PR's evidence trail and the remaining review/merge gates.

---

## 4. Acceptance Checklist

| Parent acceptance item | Current disposition | Evidence / remaining gate |
|---|---|---|
| `cron.add` via adapter proxy returns `status: ok` with a job id, not a scope/pairing error | **CLAIMED BY PARENT PR; NEEDS REVIEWER CONFIRMATION** | PR #2962 body and commit `ce39fdc...` both claim live adapter-proxy smoke passed. Codex's earlier review confirmed the gateway operation itself worked with a longer timeout, but required the script hardening now present in `ce39fdc...`. |
| Full BFF path creates a persona and registers its four OODA cron jobs in `cron.list`, not `dry_run` | **CLAIMED BY PARENT PR; NEEDS REVIEWER CONFIRMATION** | PR #2962 body claims `POST /bff/personas` returned `meta.cron_registration_mode="gateway_rpc"` and `cron_registered_count=4`, with all four jobs found and then removed. This sidecar did not reproduce the live path. |
| Scope survives `openclaw-data` volume / gateway container recreate | **CLAIMED BY PARENT PR; NEEDS REVIEWER CONFIRMATION** | PR #2962 body claims `openclaw-gateway` and `openclaw-gateway-adapter` were force-recreated with volumes preserved and the write-scope smoke passed without re-approval. If the reviewer requires proof after the `ce39fdc...` smoke-script hardening, ask the parent owner to rerun the post-refresh smoke. |
| Existing tests stay green; no docker-exec-from-BFF; no supervisor cadence change | **PARTIAL PASS / PR REVIEW GATE** | GitHub Branch CI Gate is green at the queried PR head. PR #2962 files are BFF/cron/smoke-script only, not supervisor cadence. The parent still must refresh the branch because GitHub reports `BEHIND` and the latest `origin/dev` comparison shows unrelated deletion noise. |

Recommended parent-review posture: the substantive Human/Ops blocker appears
to have moved, but the parent is not closeout-ready until PR #2962 is
refreshed, its intended diff is clean, and the reviewer accepts the post-fix
live smoke evidence.

---

## 5. Dependency Map

### Blocking / review dependencies

| Dependency | Current state | Why it matters |
|---|---|---|
| Parent PR #2962 refresh | **Required** | GitHub reports `mergeStateStatus: BEHIND`; local latest-`dev` diff shows unrelated deletion noise. This is a merge hygiene gate before approval/merge. |
| Codex parent review | **Required** | The parent reviewer requested a concrete smoke-script fix. Commit `ce39fdc...` appears to address it, but review approval is not recorded yet. |
| Live evidence acceptance | **Required** | The sidecar did not rerun live OpenClaw/BFF operations. Parent closeout should rest on the parent PR's live logs plus reviewer confirmation, not on this packet. |

### Resolved / changed dependencies

| Dependency | Current state | Note |
|---|---|---|
| Human/Ops adapter-device scope grant | **Apparently resolved per parent PR evidence** | The parent PR body says adapter write scope is confirmed durable and `cron.add` via adapter proxy works. This should be verified by reviewer from logs before final approval. |
| Zombie `origin/task/OPENCLAW-CRON-WRITE-SCOPE` branch | **No longer zombie** | The branch now backs open PR #2962. The current issue is stale base, not branch deletion. |
| Repeated no-change acceptance packets | **No longer the same condition** | Follow-up 4's throttle recommendation remains sound for no-change repeats, but this packet has a legitimate new state transition to record. Further acceptance-packet sidecars should wait for PR #2962 review/merge state to change again. |

---

## 6. Suggested Reviewer Checks

1. Confirm PR #2962 is refreshed against latest `dev` before parent approval.
2. Confirm the post-`ce39fdc...` live smoke output includes successful
   `cron.add`, `cron.list`, `cron.remove`, and a no-orphan check.
3. Confirm the full BFF persona-create evidence still stands after the smoke
   script hardening, or ask the parent owner to rerun it if the reviewer needs
   current evidence on the refreshed branch.
4. Confirm acceptance criterion 3 is interpreted as "container recreate with
   volumes preserved" unless the parent explicitly decides that a full
   `openclaw-data` volume wipe must also be rerun and proven.
5. Do not mark this parent `done` from the sidecar; parent closeout belongs to
   `Claude` after review approval and PR merge.

---

## 7. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is complete | Parent owner after review approval, PR merge, and closeout |
| That PR #2962 is merge-ready as currently based | Parent owner; it is currently `BEHIND` |
| That the sidecar independently reran live OpenClaw gateway or BFF proofs | Parent owner / reviewer |
| That canonical truth, runtime contract, router/governance, or supervisor behavior changed here | No such changes were made |
| That another follow-up sidecar should be auto-created immediately | Wait for PR #2962 review/merge state or parent task status to change again |

---

## 8. Handoff

**To**: `Claude`
**From**: `Codex`
**Requested review outcome**: Approve this sidecar if it accurately captures
the new parent movement since Follow-up 4: the parent is now `in_progress`,
PR #2962 has live-proof claims and a post-review smoke-script hardening commit,
but the PR is still `BEHIND` and must be refreshed/re-reviewed before parent
approval or closeout.

