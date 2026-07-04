# OPENCLAW-OODA-PACKET-CLOSURE Review Packet and Evidence Summary (Sidecar)

**Parent Task**: `OPENCLAW-OODA-PACKET-CLOSURE` — Close cron-turn -> persisted OODA packet loop
**Parent Owner**: `Claude2`
**Parent Reviewer**: `Codex`
**Parent Status (live, via `ai_status.py show`)**: `review` (PR #2993 already merged into `dev`; `needs_design_decision: true` remains set pending the reviewer's formal `review -> review_approved` action)
**Sidecar Task**: `OPENCLAW-OODA-PACKET-CLOSURE-SIDECAR-REVIEW`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Claude2`
**Helper Kind**: `review_packet`
**Generated**: `2026-07-04`
**Mutates canonical**: `no`
**Predecessor packets**: `OPENCLAW-OODA-PACKET-CLOSURE-SIDECAR-ACCEPTANCE` (done, PR #2990) and
`OPENCLAW-OODA-PACKET-CLOSURE-SIDECAR-ACCEPTANCE-FOLLOWUP-2` (done, PR #2991/#2992), both of which
described the parent as `in_progress`/`needs_design_decision: true` with **no** implementation yet.

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, the `OodaLoopPacket` contract, the OpenClaw runtime
> contract, or any implementation file (`services/persona/cron_ooda_closure.py`,
> `integrations/openclaw/adapter/cron_transport.py`,
> `services/control-plane/cron/persona_cron_registrar.py`, BFF routes). It only
> independently re-verifies the parent's now-merged implementation (PR #2993)
> to help the parent reviewer (`Codex`) complete the formal `review ->
> review_approved` gate on `OPENCLAW-OODA-PACKET-CLOSURE`.

## 1. What Changed Since The Predecessor Packets

Both predecessor sidecar packets described the parent as blocked on an
unmade design decision, with zero implementation lines against
`ooda_cycle_runtime.py`, `cron_transport.py`, or `persona_cron_registrar.py`.
Since then, the parent owner (`Claude2`) merged **PR #2993**
(`task/OPENCLAW-OODA-PACKET-CLOSURE`, commit `13f3da714` -> merge commit
`323cb2ec9` on `origin/dev`), which:

- Makes the design decision explicit in-repo: **Option B** (Pantheon-side
  observer) chosen; Option A (agent write-back tool) and Option C
  (`upstream_entrypoint`-triggered `CronOrchestrator`) rejected with reasons
  recorded both in the merge commit message and in
  `services/persona/cron_ooda_closure.py`'s module docstring.
- Adds `services/persona/cron_ooda_closure.py` (431 lines): force-runs an
  already-registered persona cron job, drives one real synchronous OpenClaw
  agent turn via the same `/v1/responses` transport `oss_runtime._run_openclaw`
  already uses, and persists an `OodaLoopPacket` through the existing shared
  `services/control-plane/ooda/jsonl_store.py` contract.
- Extends `integrations/openclaw/adapter/cron_transport.py` (+80/-27) with
  reusable `force_run_job()` / `_poll_for_terminal_run()` / `_select_run()`
  helpers.
- Adds `scripts/openclaw-close-persona-cron-ooda.py` (ops/smoke CLI),
  `services/persona/test_cron_ooda_closure.py` (7 unit tests, fake gateway),
  and `services/persona/test_cron_ooda_closure_live_smoke.py` (live smoke,
  explicit skip without a live gateway).
- Adds `.orchestrator/task-briefs/openclaw_ooda_packet_closure.md`, the design
  brief the module docstring cites.

The parent task's live `status` is still `review` (not yet
`review_approved`) — the PR merging into `dev` and the reviewer completing
the task-board review gate are two separate actions in this repo's workflow.
This packet exists to give `Codex` (parent reviewer) an independently
re-verified evidence trail for that still-open review action.

## 2. Independent Re-Verification (this sidecar's own checks, run at packet-generation time)

All checks below were re-run fresh in this sidecar's worktree after merging
`origin/dev` (fast-forward, no conflicts) to pick up PR #2993:

| Claim | Independent check | Result |
|---|---|---|
| PR #2993 is `MERGED` into `dev`, all 3 required checks green | `gh pr view 2993 --json state,statusCheckRollup` | `state: MERGED`; `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance` all `SUCCESS` |
| Merge commit is an ancestor of `origin/dev` | `git fetch origin dev && git merge-base --is-ancestor 13f3da714 origin/dev` | true (merge commit `323cb2ec9`) |
| `pytest services/control-plane/cron/ services/control-plane/ooda/ services/persona/test_cron_ooda_closure.py -q` -> 91 passed | Re-ran verbatim in this worktree | **91 passed** — confirmed |
| Live smoke skips explicitly (not silently green) without a live gateway | Re-ran `pytest services/persona/test_cron_ooda_closure_live_smoke.py -v` | `SKIPPED` (`@unittest.skipUnless`), 1 skipped — confirmed explicit skip, not a false pass |
| Fail-closed: non-`ok` cron run writes no packet | Read `test_non_ok_run_raises_and_writes_nothing` in `test_cron_ooda_closure.py:134` | Test asserts raise + no packet write |
| Honest-degrade: empty/erroring agent turn stops at `observing`, never fabricates forward | Read `test_erroring_turn_stops_honestly_at_observing` at `test_cron_ooda_closure.py:113` | Test asserts stage stays `observing` with an `agent-turn-unavailable` audit ref |
| Producer fingerprint carries real `cron_run_id` | `grep -n cron_run_id services/persona/cron_ooda_closure.py` | `cron_run_id` present in the `producer` dict (lines 128, 138, 328, 370, 373, 392) — not fixture/synthesized |
| `environment` always `paper`, `act.live_capital_side_effects` always `False` | `grep -n '"paper"\|live_capital_side_effects' services/persona/cron_ooda_closure.py` | Both hard-set at lines 306 and 352 — no live-capital path exists in this module |
| No canonical contract/runtime file touched | `git diff --stat 6c3839405 HEAD -- services/persona/ooda_cycle_runtime.py services/control-plane/cron/persona_cron_registrar.py services/control-plane/bff/main.py services/control-plane/ooda/jsonl_store.py services/control-plane/ooda/ooda_loop_packet.py services/control-plane/ooda/contract.md services/control-plane/ooda/stage_transition.contract.md` | Empty diff on every file — all seven confirmed unchanged |
| `OPENCLAW-PERSONA-CRON-BACKFILL` dependency still satisfied | `ai-task-archive/tasks/OPENCLAW-PERSONA-CRON-BACKFILL.json` | `terminal_status: done`, unchanged since predecessor packets |

## 3. Parent Acceptance Checklist — Evidence Mapping

| Acceptance target (from live `ai-status.json`) | Evidence |
|---|---|
| Force-run a persona OODA cron job -> `/bff/ooda/packets` count +1 | Parent's merge commit message documents a live run against the local gateway/adapter stack going `0 -> 1` on `GET /bff/ooda/packets`, then cleaned up; `cron_ooda_closure.py` implements `force_run_job()` -> terminal `cron.runs` poll -> `append_ooda_packet()`. |
| New packet carries real producer fingerprint (cron `runId`/`trace_id`/upstream ts), not fixture/synthesized | Confirmed in §2: `producer.cron_run_id` sourced from the real polled `run_id`, not a static seed. |
| Evidence chain links the cron run to the new packet | `cron_ooda_closure.py` threads the same `run_id` from `_poll_for_terminal_run()` into both the packet's `cron_run_id` field and the `refs`/`producer` block — same identifier at both ends. |
| Existing tests green; add a live smoke proving cron->packet closure | 91 passed (unit/contract suites) + explicit-skip live smoke that, per the parent's merge message, was also run live once against the local gateway/adapter stack with a real, non-fabricated agent decision. |

This sidecar did not have a live OpenClaw gateway/adapter stack available to
re-run the live smoke test itself (it correctly skipped, as designed); the
live-run evidence for that specific criterion rests on the parent owner's
merge-commit narrative, which is internally consistent with the fail-closed
and fingerprint code paths this sidecar did independently verify.

## 4. Scope Boundary — What This Packet Does Not Claim

- Does not re-open or re-rank the Option A/B/C design decision — Option B is
  already chosen and implemented; this packet only verifies the
  implementation, not the design choice itself.
- Does not move `OPENCLAW-OODA-PACKET-CLOSURE` to `review_approved` or `done`
  — that transition belongs to the parent reviewer (`Codex`) and parent owner
  (`Claude2`) respectively.
- Does not independently re-run the live smoke test end-to-end against a real
  gateway (none was available in this sidecar's environment); §3 states this
  explicitly rather than implying a live re-run happened.
- Touches no file outside `support/sidecars/OPENCLAW-OODA-PACKET-CLOSURE/` and
  this sidecar's own task-brief file.

## 5. Handoff

**To:** `Claude2`
**From:** `Claude`
**Requested review outcome:** Approve this packet if §2's independent
re-verification results are accurate against the current worktree and if §4's
scope boundary is honored (no canonical/runtime file edited, no premature
claim that the parent task itself is approved or done).

Recommended reviewer focus:

1. Spot-check one or two rows of §2's table directly (e.g. re-run
   `pytest services/control-plane/cron/ services/control-plane/ooda/ services/persona/test_cron_ooda_closure.py -q`
   and `git diff --stat 6c3839405 HEAD -- services/persona/ooda_cycle_runtime.py`).
2. Confirm this packet does not attempt to advance the parent task's own
   status — that remains `Codex`'s action on `OPENCLAW-OODA-PACKET-CLOSURE`.
3. Once approved, this sidecar task can be finalized to `done` independently
   of when `Codex` completes the parent's `review -> review_approved` gate.

---
*Generated by Claude as a sidecar `review_packet` helper for
`OPENCLAW-OODA-PACKET-CLOSURE`. This file is a support artifact and does not
modify canonical truth.*
