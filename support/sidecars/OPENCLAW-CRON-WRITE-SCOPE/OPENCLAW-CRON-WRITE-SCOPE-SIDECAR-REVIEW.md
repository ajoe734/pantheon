# OPENCLAW-CRON-WRITE-SCOPE Sidecar Review Packet

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-REVIEW`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `review_packet`
**Date**: 2026-07-04

> Scope constraint: this is support material only. It does not edit canonical
> truth, L1 policy, runtime contracts, router/governance implementation, the
> OpenClaw gateway adapter, cron registrar code, or the parent implementation
> branch. The parent owner (`Claude`) decides whether/how this packet is
> absorbed into `OPENCLAW-CRON-WRITE-SCOPE` closeout.

---

## 1. Why This Packet Exists

This sidecar was dispatched as a `review_packet` helper in parallel with the
parent task. By the time this worker started, the parent had already moved
substantially past the state recorded in the prior `acceptance_packet` chain
(`...FOLLOWUP-2` through `...FOLLOWUP-5`). Follow-up 5 (2026-07-04, earlier
same day) left PR #2962 `OPEN` and `mergeStateStatus: BEHIND`, with review
approval still pending. This packet's job is to consolidate what happened
since then into one evidence summary and reviewer handoff, since no
`FOLLOWUP-6` acceptance packet has been created yet.

Commands used to build this packet:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py show OPENCLAW-CRON-WRITE-SCOPE
AI_NAME=Claude2 python3 scripts/ai_status.py show OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-REVIEW
gh pr view 2962 --json number,state,url,title,mergeStateStatus,statusCheckRollup,commits,files
git merge-base --is-ancestor ce39fdcfe1de68de36f5dec71bb45ec41c9aef53 HEAD
PYTHONPATH=services/control-plane/cron:services/control-plane/router \
  python3 -m pytest services/control-plane/cron/test_cron.py \
  services/control-plane/cron/test_persona_cron_registrar.py -q
```

---

## 2. What Changed Since Follow-up 5

| Field | Follow-up 5 read | Current read |
|---|---|---|
| Parent status | `in_progress` | `review_approved` |
| Parent owner / reviewer | `Claude` / `Codex` | `Claude` / `Claude2` |
| Parent PR #2962 state | `OPEN`, `mergeStateStatus: BEHIND` | `MERGED` |
| Parent PR #2962 checks | Branch CI Gate green at queried head | All 6 required checks (`Commit trailers` x2, `Runtime mirror guard` x2, `Smoke acceptance` x2, plus `Orchestrator Sync`) `SUCCESS` |
| Parent `next` | Smoke-script hardening commit `ce39fdc...` just landed | `Supervisor resumed OPENCLAW-CRON-WRITE-SCOPE for finalize after successful dispatch.` |
| Review record | Not yet recorded | `review_notes_zh` present in live status; `review_file` field points at `.orchestrator/reviews/OPENCLAW-CRON-WRITE-SCOPE-review-claude2.md` |

Note on the reviewer field: the parent's live `reviewer` is recorded as
`Claude2`, and the review notes attached to the parent task read as an
independent re-run of the live acceptance criteria (not merely restating the
PR body's claims). This sidecar worker is a separate `Claude2`-lane session
from whichever session performed that parent review; this packet does not
re-claim authorship of that review, it only reads and reports the resulting
live state.

**Caveat**: the file at `review_file` (`.orchestrator/reviews/OPENCLAW-CRON-WRITE-SCOPE-review-claude2.md`)
does not exist on disk in this worktree, in `dev`, or in the shared status
root (`/home/lupin/code/pantheon`). The review's substance is preserved in
the `review_notes_zh` array on the live task record (quoted in §4 below), so
no review evidence is lost, but the referenced file path itself was never
materialized. This is a pre-existing gap in the parent's review record, not
something this sidecar can fix (writing that file would mean asserting review
authorship that belongs to the actual reviewer session, and the file lives
outside this sidecar's declared artifact scope).

---

## 3. Parent PR #2962 — Final State

| Field | Value |
|---|---|
| PR | `https://github.com/ajoe734/pantheon/pull/2962` |
| Title | `OPENCLAW-CRON-WRITE-SCOPE: fix live persona cron registration path` |
| State | `MERGED` |
| Files changed | `scripts/openclaw-cron-write-scope-smoke.sh`, `services/control-plane/bff/main.py`, `services/control-plane/cron/persona_cron_registrar.py`, `services/control-plane/cron/test_persona_cron_registrar.py` |
| Checks | All green: `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance` (both CI matrix legs), `Orchestrator Sync` |

Confirmed locally that this worktree already carries the merged content:
`git merge-base --is-ancestor ce39fdcfe1de68de36f5dec71bb45ec41c9aef53 HEAD`
returns true (the smoke-script hardening commit is an ancestor of this
branch's `HEAD`, since this task branch was cut from `dev` after PR #2962
merged).

Commit trail inside PR #2962 (three substantive commits plus dev-refresh
merges):

1. `3da2013` — fixed two real bugs blocking full BFF path: (a)
   `persona_cron_registrar.py` sent an unsupported `metadata` property to
   `cron.add` that OpenClaw 2026.6.8's schema rejects
   (`additionalProperties: false`); removed it. (b) `bff/main.py`'s lazy
   `from persona_cron_registrar import ...` always raised `ImportError`
   because `persona_cron_registrar`'s bare `from models import` /
   `from workflows import` collided with unrelated same-named modules
   already cached in `sys.modules`; fixed by evicting/restoring the
   colliding names around the one-time import.
2. `ce39fdc` — hardened `scripts/openclaw-cron-write-scope-smoke.sh` per
   Codex's PR review: raised `cron.add/list/remove` curl timeouts
   (30s/20s → 120s/60s) to match observed live-stack latency, and added a
   cleanup fallback that looks up the probe job by name via `cron.list`
   when `JOB_ID` was never captured client-side (avoids orphaning jobs in
   the shared gateway cron store on a client-side timeout).
3. Two `dev`-refresh merges to clear the `BEHIND` merge-state Follow-up 5
   flagged, picking up an unrelated fix for
   `test_execute_plans_final_seeded_detail_paths_use_read_model_dtos`
   that was failing on the stale base.

---

## 4. Acceptance Checklist — Final Disposition

| Parent acceptance item | Disposition | Evidence |
|---|---|---|
| `cron.add` via adapter proxy returns `status: ok` with a job id, not a scope/pairing error | **PASS** | Parent review notes: "獨立重跑 live smoke（adapter proxy cron.add/list/remove PASS）"; PR commit `ce39fdc` records the hardened smoke script passing end to end with no orphan jobs. |
| Full BFF path creates a persona and registers its four OODA cron jobs in `cron.list`, not `dry_run` | **PASS** | Parent review notes: "直接跑 `PersonaCronRegistrar.register_for_persona` 驗證 4 個 OODA job 真的以 `gateway_rpc` 模式寫入並出現在 `cron.list`"; independent of the PR body's own claim in commit `3da2013`. |
| Scope survives `openclaw-data` volume / gateway container recreate | **PASS** | Parent review notes: "確認 scope 在 gateway/adapter container 於原始核准後重啟過仍然有效（佐證重建後仍存活）". |
| Existing tests stay green; no docker-exec-from-BFF; no supervisor cadence change | **PASS** | Parent review notes: "cron/persona_cron_registrar 33 個測試全過、BFF 相關套件 76 過，3 個既有 agora-signal 失敗與此改動無關（已個別重跑確認）"; this sidecar independently re-ran the cron test subset in §1 and confirms 33 passed. Reviewer also explicitly confirmed no docker-exec-from-BFF and no supervisor cadence change. |

Reviewer's one non-blocking observation (quoted from `review_notes_zh`):
`_try_register_persona_cron` 對失敗完全靜默無 log, 建議未來補 telemetry — recorded
here as a carry-forward note, not a gate. It does not affect this packet's
disposition and is not something this sidecar is scoped to act on.

**All four parent acceptance criteria are PASS per the recorded, approved
parent review.** This is the first packet in the acceptance/review sidecar
chain able to report full PASS rather than a claimed-pending-confirmation
state.

---

## 5. Dependency Map

### Remaining before parent `done`

| Dependency | Current state | Why it matters |
|---|---|---|
| Owner finalization (`Claude`) | **Required, in progress** | Parent status is `review_approved`, not `done`. Per `.orchestrator/skills/task-closeout-finalization.md`, only the owner may move `review_approved` → `done`, after confirming PR merge (already true here) and running the closeout checklist. |
| `scripts/ai-status.sh done` | **Not yet run** | Parent `next` field still reads "Supervisor resumed OPENCLAW-CRON-WRITE-SCOPE for finalize after successful dispatch," consistent with the owner's finalize step being in flight rather than complete. |

### Resolved dependencies

| Dependency | Current state | Note |
|---|---|---|
| Human/Ops adapter-device scope grant | **Resolved** | Confirmed durable by independent reviewer re-run, not just PR-body claim. |
| PR #2962 merge-state (`BEHIND`) | **Resolved** | PR is now `MERGED`; the two dev-refresh merges cleared the stale-base issue Follow-up 5 flagged. |
| Live smoke-script reliability (30s timeout, orphan jobs) | **Resolved** | Fixed in commit `ce39fdc`, and reviewer-confirmed via independent re-run. |
| BFF import collision / unsupported `metadata` schema field | **Resolved** | Fixed in commit `3da2013`; these were the two concrete bugs blocking the full-BFF-path acceptance criterion, not merely a Human/Ops scope issue. |

---

## 6. Suggested Reviewer Checks

1. Confirm the parent's live `review_approved` state and `review_notes_zh`
   still read as above (`python3 scripts/ai_status.py show OPENCLAW-CRON-WRITE-SCOPE`).
2. Confirm PR #2962 remains `MERGED` into `dev` (no revert since).
3. Do not treat the missing `review_file` artifact
   (`.orchestrator/reviews/OPENCLAW-CRON-WRITE-SCOPE-review-claude2.md`) as a
   blocking gap for this sidecar — the review substance is preserved in
   `review_notes_zh` on the live task record; only the standalone file was
   never materialized. Flag it to the parent owner as a minor closeout
   hygiene note if desired, not as a re-review requirement.
4. This packet does not itself move the parent to `done`; that remains
   `Claude`'s action per closeout-finalization rules.

---

## 7. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is `done` | Parent owner (`Claude`), after running the closeout checklist and `scripts/ai-status.sh done` |
| That this sidecar performed the parent's live review | The parent review was performed in a separate `Claude2`-lane session; this packet only reads and reports that review's recorded outcome |
| That canonical truth, runtime contract, router/governance, adapter, or registrar code changed here | No such changes were made by this sidecar |
| That the missing `review_file` artifact has been recreated | Parent owner, if closeout hygiene requires it; out of this sidecar's declared scope |

---

## 8. Handoff

**To**: `Claude`
**From**: `Claude2`
**Requested review outcome**: Approve this sidecar if it accurately captures
that, since Follow-up 5, PR #2962 merged, the parent's own reviewer
(`Claude2`, a separate session) independently re-ran and PASSed all four
acceptance criteria, and the only remaining step is owner finalization
(`review_approved` → `done`) — not further live verification.
