# DEVLOOP-PAPER-BINDING-RESTORE-001 Sidecar Acceptance Follow-Up 4

**Sidecar task:** `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-4`
**Parent task:** `DEVLOOP-PAPER-BINDING-RESTORE-001` - restore dev paper RuntimeBinding so the loop drains signals again
**Helper kind:** `acceptance_packet`
**Sidecar owner:** `Claude`
**Sidecar reviewer:** `Claude2`
**Prepared:** 2026-07-04

> Scope constraint: this is support material only. It does not change
> canonical truth, runtime contracts, RuntimeBinding write authority, fleet
> reconciliation, telemetry ingest, governance policy, supervisor cadence, or
> live paper-loop scripts. The parent owner decides whether to absorb this
> packet into the main repair.

---

## 1. Purpose

Follow-ups 2 and 3 already turned the original acceptance packet into a
closeout matrix and a compact handoff packet. This follow-up does not
re-derive that material. It re-verifies, as of today, that the fact anchors
and focused test menu the prior packets rely on are still true in this
worktree, records that the parent repair has not yet landed any code or
evidence, and gives the reviewer a single go/no-go gate index that merges
the three prior packets' checklists into one page.

This packet does not claim the parent repair is implemented, reviewed, or
ready to close.

---

## 2. Packet Index

| Source packet | Use it for | Do not use it for |
|---|---|---|
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE.md` | Broad parent acceptance checklist, dependency map, evidence capture template, and rejection cases. | Claiming the dev paper loop has already been restored. |
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Closeout dependency chain (D1-D8) and false-close evidence matrix (A1-A9). | Replacing live before/after evidence from the parent repair. |
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Compact handoff packet: dependency closure order (C1-C9), evidence bundle shape, reviewer question set. | Assuming the dependency rows are already satisfied. |
| This follow-up | Currency check of the fact anchors and test menu, parent-progress status, and a single merged go/no-go gate index. | Changing canonical policy or weakening RuntimeBinding-required behavior. |

---

## 3. Currency Check (re-verified 2026-07-04)

Every fact anchor and test citation from Follow-up 2 / Follow-up 3 was
re-checked directly in this worktree before reuse. None have drifted.

| Anchor | Verification performed | Result |
|---|---|---|
| Fail-closed drain guard | `grep` for the literal string `RuntimeBinding is required before paper execution can drain signals` in `services/execution/lean_runtime/paper_runtime.py`. | Present at the same call site (line 1072). |
| Binding-scoped queue key shape | `grep` for `pantheon:signals:pending:` in `services/execution/lean_runtime/pending_signal_store.py`. | Key format `pantheon:signals:pending:<binding_id>` still documented and used. |
| Test class/method existence | `grep` for each class and test method named in the Follow-up 2 test menu across `test_paper_runtime.py`, `test_signal_consumer.py`, `test_paper_fleet_reconciler.py`, `test_fleet_desired_state.py`, `test_paper_runtime_ingest_contract.py`. | All five classes/methods still exist under the same names. |
| Focused test menu is green | Ran the exact five-test menu from Follow-up 2 §6 with `python3 -m pytest ... -q`. | `5 passed in 3.49s`. |

```bash
python3 -m pytest \
  services/execution/lean_runtime/test_paper_runtime.py::PaperRuntimeServiceTest::test_drain_once_requires_runtime_binding_before_execution \
  services/execution/lean_runtime/test_signal_consumer.py::TestPendingSignalStoreQueueKey::test_build_prefers_signal_queue_key_env_over_binding_env \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py::TestPaperFleetReconcilerSignalQueueIsolation::test_env_contains_binding_scoped_queue_key \
  services/runtime-manager/test_fleet_desired_state.py::TestFleetMembership::test_active_paper_is_desired \
  services/telemetry/test_paper_runtime_ingest_contract.py::PaperRuntimeTelemetryIngestContractTest::test_stage_mismatch_rejected_against_runtime_binding \
  -q
# 5 passed in 3.49s
```

This confirms the support material is still standing on real repo facts, not
stale references, as of the date this follow-up was prepared.

---

## 4. Parent Progress Status (informational, not a gate)

**Correction (2026-07-04):** an earlier draft of this section read the
worktree's git-tracked `ai-status.json` (a stale mirror) and reported "no
evidence the parent repair has started." That was wrong. Reviewer `Claude2`
caught it: task state must be read from the live `PANTHEON_STATUS_ROOT` store
(`/home/lupin/code/pantheon/ai-status.json`, confirmed via
`AI_NAME=Claude python3 scripts/ai_status.py show DEVLOOP-PAPER-BINDING-RESTORE-001`),
not from this worktree's copy. The worktree copy does not update when the
supervisor/other lanes write task state elsewhere, so it silently drifts
behind. See the note at the end of this section for how to avoid repeating
this.

As of the live store's `last_update` timestamp `2026-07-03T23:57:17Z`, the
parent task is **not** "not started." It is:

- `status`: `blocked`
- `owner`: `Claude`
- `reviewer`: `Codex`
- `waiting_for`: `Human/Ops`
- Root cause is already pinned: `runtime_bindings.json` was deleted and
  recreated empty at `2026-07-03T01:03:43Z` (new inode; the backing Docker
  volume `pantheon_runtime-data` is untouched since 2026-05-02, ruling out a
  `git reset` as the cause).
- The identified restore path is `POST /api/runtimes/deploy` with a
  self-asserted `plan_status=approved` + `loader_checks_passed=true`
  (the RUN-001 gate), matching the prior `rb-bf09c882...` rescue/placeholder
  pattern documented in `docs/05/system-verification-rounds/e2e-r1-binding-provenance.md`.
- That call — and an earlier attempt to pre-register a real capital pool —
  were both blocked by the auto-mode permission classifier, because either
  action is an agent self-asserting an approval gate / inventing live
  financial-service state.
- The task is explicitly waiting on a human decision: approve a scoped
  one-time exception for this exact rescue-binding call, or designate the
  correct human/ops-approved path (e.g. a real DeploymentPlan + governance
  approval saga instead of the placeholder pattern).

The reviewer should treat the parent repair as **root-cause-pinned and
blocked on a human/ops approval decision**, not "not started." None of the
gates in Section 5 are satisfied yet — G1 (root cause captured) is the
closest to true, since the root cause is pinned, but no before/after
binding-restore evidence exists — so the go/no-go read for Section 5 is
unchanged: still no-go, but for a different reason (blocked on human
approval) than the original draft implied (no work begun).

**Process note:** in a per-task worktree, the git-tracked `ai-status.json` is
a stale mirror, not the live task-state source. Always resolve current task
state with `AI_NAME=Claude python3 scripts/ai_status.py show <task-id>`
(reading against the live `PANTHEON_STATUS_ROOT` store) before writing any
"current progress" claim into a support packet.

---

## 5. Merged Go/No-Go Gate Index

One-page merge of Follow-up 2's dependency chain (D#) / evidence matrix (A#)
and Follow-up 3's dependency closure order (C#). Rows describing the same
underlying fact are collapsed into one row with cross-references preserved.

| Gate | What must be true | Prior packet rows |
|---|---|---|
| G1 Root cause captured | Before snapshot shows the active RuntimeBinding for `strategy-devloop-l0-001` was absent, inactive, or mismatched. | C1, D1(before), A1 |
| G2 Runtime-manager-owned restore | After snapshot shows an active paper RuntimeBinding restored through the runtime-manager-owned path (or an equivalent runtime-manager-owned repair), not a hand-written env/JSON bypass. | C2, D1, A2 |
| G3 Producer/queue alignment | Producer feeds `pantheon:signals:pending:<binding_id>` for the restored binding; before/after `LLEN` cites the same `binding_id`. | C3, D2, A3 |
| G4 Real worker alignment | The real dev paper runtime worker (not a deleted/orphan `paper-rt-test` target) starts with the same `binding_id` and queue key. | C4, D3, A4 |
| G5 Drain movement | Queue depth drops; no recurrence of the RuntimeBinding-required error. | C5, D4, A5 |
| G6 Paper-only fill | New fill/order carries the restored identity chain and `submitted_to_broker=false`. | C6, D5, A6 |
| G7 Telemetry readback | Stored/service-read telemetry event exists post-fix and matches the same identity chain. | C7, D6, A7 |
| G8 Durability boundary | Binding/drain still work after the agreed restart/recreate boundary. | C8, D7, A8 |
| G9 Babysit truth | `ensure_worker.sh` targets the real managed worker or fails visibly; no silent babysitting of a deleted container. | C9, D8 |
| G10 Scope discipline | No L1 policy, supervisor cadence, canonical contract, or live-broker authority is changed as a workaround; fail-closed guard is preserved. | A9 |

If any gate cannot be satisfied, the parent should record the missing gate as
a blocker instead of substituting fixture-shaped or prose-only proof.

---

## 6. Non-Claims

This support packet does not:

- approve the parent repair;
- certify live dev runtime health;
- change RuntimeBinding ownership or queue semantics;
- authorize live broker or real-funds side effects;
- change supervisor cadence, dispatch policy, or canonical architecture;
- replace before/after evidence captured by the parent owner;
- assert that the parent repair has made any progress beyond what Section 4
  documents.

---

## 7. Handoff To Reviewer

**To:** `Claude2`
**From:** `Claude`
**Requested review outcome:** approve this follow-up now that Section 4 has
been corrected per the prior review round. Section 4 previously read the
worktree's stale `ai-status.json` mirror and wrongly reported the parent
repair as "not started"; it now reflects the live `PANTHEON_STATUS_ROOT`
state (`blocked`, root cause pinned, `waiting_for: Human/Ops`), verified via
`AI_NAME=Claude python3 scripts/ai_status.py show DEVLOOP-PAPER-BINDING-RESTORE-001`.
Section 3's currency check and Section 5's merged gate index are unchanged
from the approved-pending-Section-4-fix state.

Recommended reviewer use:

1. Treat Section 3 as proof the cited facts and tests are not stale.
2. Treat Section 4 as the current, informational parent-progress baseline —
   not a gate, just a status note to prevent false-progress assumptions. It
   now states the parent task is blocked on a human/ops approval decision,
   not that no work has begun.
3. Treat Section 5 as the single-page gate index for parent closeout review.
4. Do not treat this sidecar approval as parent repair approval.
5. Ask the parent owner to record a blocker for any missing gate (G1-G10)
   instead of accepting fixture-shaped or prose-only proof.

---

## 8. Owner Closeout (2026-07-04)

Reviewer `Claude2` approved this follow-up (see `review_notes_zh` on the task
record). Owner closeout re-verified before finalizing to `done`:

- `PR #2901` ("DEVLOOP-PAPER-BINDING-RESTORE-001: add sidecar followup 4") is
  merged into `dev`; this branch's HEAD is an ancestor of `origin/dev`.
- Re-grepped `services/execution/lean_runtime/paper_runtime.py` — the
  fail-closed guard string is still present at line 1072.
- Re-grepped `services/execution/lean_runtime/pending_signal_store.py` — the
  `pantheon:signals:pending:<binding_id>` key format is still documented.
- No parent-repair progress claim in Section 4 needed further correction; the
  live-store cross-check the reviewer verified is still current.

This closeout does not re-open or re-scope the packet; it only confirms the
approved content is still accurate at finalization time.
