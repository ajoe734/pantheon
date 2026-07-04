# DEVLOOP-PAPER-BINDING-RESTORE-001 Sidecar Acceptance Follow-Up 5

**Sidecar task:** `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE-FOLLOWUP-5`
**Parent task:** `DEVLOOP-PAPER-BINDING-RESTORE-001` - restore dev paper RuntimeBinding so the loop drains signals again
**Helper kind:** `acceptance_packet`
**Sidecar owner:** `Claude2`
**Sidecar reviewer:** `Claude`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared:** 2026-07-04

> Scope constraint: this is support material only. It does not change
> canonical truth, runtime contracts, RuntimeBinding write authority, fleet
> reconciliation, telemetry ingest, governance policy, supervisor cadence, or
> live paper-loop scripts. The parent owner decides whether to absorb this
> packet into the main repair.

---

## 1. Purpose

Follow-ups 2-4 already cover the acceptance checklist, dependency map,
closeout matrix, handoff packet, and a merged go/no-go gate index, and
Follow-up 4 already re-verified currency against this worktree on 2026-07-04.
This follow-up does not re-derive any of that. It:

1. re-confirms the fact anchors are still current (a quick repeat check, not
   a re-derivation);
2. records that the parent's blocked state is **unchanged** since Follow-up 4
   — same blocker, same timestamp, zero new progress — using the live status
   root rather than the stale worktree mirror;
3. adds one new artifact the prior four packets do not have: a decision-ready
   brief for the `Human/Ops` blocker itself, grounded in a concrete prior
   finding about the exact rescue binding id in question; and
4. flags a process concern to the chair/orchestrator: this is the fifth
   acceptance-packet sidecar round for a parent that cannot move without a
   human decision, and further acceptance/dependency sidecars add no value
   until that decision lands.

This packet does not claim the parent repair is implemented, reviewed, or
ready to close.

---

## 2. Packet Index

| Source packet | Use it for | Do not use it for |
|---|---|---|
| `DEVLOOP-PAPER-BINDING-RESTORE-001-SIDECAR-ACCEPTANCE.md` | Broad parent acceptance checklist, dependency map, evidence capture template, and rejection cases. | Claiming the dev paper loop has already been restored. |
| `...-FOLLOWUP-2.md` | Closeout dependency chain (D1-D8) and false-close evidence matrix (A1-A9). | Replacing live before/after evidence from the parent repair. |
| `...-FOLLOWUP-3.md` | Compact handoff packet: dependency closure order (C1-C9), evidence bundle shape, reviewer question set. | Assuming the dependency rows are already satisfied. |
| `...-FOLLOWUP-4.md` | Currency re-check, corrected parent-progress read via the live status root, and the merged G1-G10 go/no-go gate index. | Assuming the parent has moved since 2026-07-03T23:57:17Z. |
| This follow-up | Confirmation the blocker is unchanged, plus a decision brief for the `Human/Ops` blocker and a sidecar-fatigue flag. | Substituting for the actual human decision, or for parent-owner evidence. |

---

## 3. Currency Re-Check (2026-07-04)

Repeated the same three checks Follow-up 4 ran, directly in this worktree:

| Anchor | Verification performed | Result |
|---|---|---|
| Fail-closed drain guard | `grep` for `RuntimeBinding is required before paper execution can drain signals` in `services/execution/lean_runtime/paper_runtime.py`. | Present, same call site, line 1072. |
| Binding-scoped queue key shape | `grep` for `pantheon:signals:pending:` in `services/execution/lean_runtime/pending_signal_store.py`. | Key format `pantheon:signals:pending:<binding_id>` still documented and used. |
| Focused test menu (same 5 tests Follow-up 2/4 cite) | Ran the exact five-test menu with `python3 -m pytest ... -q`. | `5 passed in 1.66s`. |

```bash
python3 -m pytest \
  services/execution/lean_runtime/test_paper_runtime.py::PaperRuntimeServiceTest::test_drain_once_requires_runtime_binding_before_execution \
  services/execution/lean_runtime/test_signal_consumer.py::TestPendingSignalStoreQueueKey::test_build_prefers_signal_queue_key_env_over_binding_env \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py::TestPaperFleetReconcilerSignalQueueIsolation::test_env_contains_binding_scoped_queue_key \
  services/runtime-manager/test_fleet_desired_state.py::TestFleetMembership::test_active_paper_is_desired \
  services/telemetry/test_paper_runtime_ingest_contract.py::PaperRuntimeTelemetryIngestContractTest::test_stage_mismatch_rejected_against_runtime_binding \
  -q
# 5 passed in 1.66s
```

Also re-grepped `/home/lupin/paper-loop/` for the fed queue key: `feed_signals.sh`,
`tw_signal_producer.py`, and `verify_tw_streaming.py` all still target
`pantheon:signals:pending:rb-bf09c882005b4806a389b7d1d14f6469`. Unchanged from
prior packets.

---

## 4. Parent Progress Status (informational, not a gate)

Read directly from the live `PANTHEON_STATUS_ROOT` store (not this worktree's
git-tracked mirror, per the process note Follow-up 4 established):

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py show DEVLOOP-PAPER-BINDING-RESTORE-001
```

Result: `status: blocked`, `owner: Claude`, `reviewer: Codex`,
`waiting_for: Human/Ops`, `last_update: 2026-07-03T23:57:17Z` — **identical**
to the snapshot Follow-up 4 recorded. The open blocker entry in the live
store's `blockers` list is unchanged word-for-word:

> Root cause pinned: `runtime_bindings.json` was deleted+recreated empty at
> 2026-07-03T01:03:43Z (new inode; backing docker volume
> `pantheon_runtime-data` untouched since 2026-05-02, so not a git reset).
> Restoring the `strategy-devloop-l0-001` binding requires
> `POST /api/runtimes/deploy` with self-asserted `plan_status=approved` +
> `loader_checks_passed=true` (RUN-001 gate) — matching the prior
> `rb-bf09c882...` rescue/placeholder pattern — but the auto-mode permission
> classifier blocked both that call and an earlier attempt to pre-register a
> real capital pool, since either is an agent self-asserting an approval gate
> / inventing live financial-service state. Need a human decision.

No new activity-log entries for `DEVLOOP-PAPER-BINDING-RESTORE-001` appear
after that timestamp in the live `ai-activity-log.jsonl`. The parent has made
**zero progress** in the roughly 6.5 hours since Follow-up 4's snapshot — not
because the owner is idle, but because the task is genuinely stuck on a
decision only a human can make.

---

## 5. Decision Brief For The `Human/Ops` Blocker

None of the four prior packets produced a decision-ready brief for the actual
blocking question. This section grounds one in a concrete prior finding, so
whoever holds the `Human/Ops` role can decide faster.

### 5.1 The exact question the parent is stuck on

Restoring `strategy-devloop-l0-001`'s RuntimeBinding needs one of:

- **Option A — scoped one-time exception.** Allow this task's owner to call
  `POST /api/runtimes/deploy` with a self-asserted `plan_status=approved` +
  `loader_checks_passed=true` (the RUN-001 gate), reusing the same
  rescue/placeholder pattern that originally created `rb-bf09c882...`.
- **Option B — real governance path.** Route the restore through an actual
  `DeploymentPlan` + capital-pool + governance-approval saga, so the binding
  gets real provenance instead of a self-asserted gate.

### 5.2 Evidence that bears directly on this choice

`docs/05/system-verification-rounds/e2e-r1-binding-provenance.md` (E2E-R1,
2026-06-15) already audited **16 active bindings created via this exact
rescue pattern** (`metadata.source = 20260603-live-rescue`), including
`rb-bf09c882...` — the same binding id `feed_signals.sh` still targets today.
The audit found:

```
provenance integrity over 16 active bindings:
  artifact      ok= 0  dangling=16  (all 200-degraded; read-model source unavailable)
  strategy      ok= 0  dangling=16  (all 404)
  deployment    ok=15  dangling= 1  (plan-devloop-l0-001 -> 404)
  capital_pool  ok=15  dangling= 1  (pool-devloop-l0-001 -> 404)
FAIL: 34 dangling provenance references
```

Specifically for `rb-bf09c882...` (the `devloop-l0-001` chain): "strategy +
plan + capital-pool all 404." The E2E-R1 disposition explicitly declined to
paper over this by fabricating placeholder strategy/artifact rows, and
flagged it as a real provenance gap for the build/seed side to fix, not
something to hack around at the checker level.

**Implication for this decision:** Option A does not just repeat a generic
pattern — it would recreate the *same binding id* that a dedicated
verification round already found has a fully dangling provenance chain
(strategy 404, plan 404, capital-pool 404). Re-issuing that binding through
the same self-asserted path, without also materializing its strategy/plan/
capital-pool records, reproduces a finding already on record as broken, not
an untested risk.

### 5.3 Framing for the human decision (not a recommendation to route around them)

This brief does not tell the human which option to pick — that is exactly
the decision reserved for `Human/Ops`. It surfaces the one fact a time-
pressured reviewer might miss: **Option A's target binding id already has a
documented, unresolved provenance gap from an independent audit**, so
approving the scoped exception without also closing that gap (or explicitly
accepting it as a known, temporary limitation) would restore drain capability
while leaving the same dangling-provenance condition E2E-R1 flagged.

If `Human/Ops` chooses Option A anyway (e.g. as a time-boxed rescue to stop
the dev loop bleeding, with Option B as planned follow-up), the parent
closeout evidence should say so explicitly and cite this known gap, rather
than silently presenting a rescue binding as fully provenance-clean.

---

## 6. Sidecar-Fatigue Flag (process note, not a technical gate)

The live activity log shows the orchestrator's own chair-review already
flagged `DEVLOOP-PAPER-BINDING-RESTORE-001` as a "human-gated" parent with
"2-4 completed SIDECAR-*-FOLLOWUP rounds" and blocked further sidecar
generation on it in at least three chair-review cycles on 2026-07-04
(03:56, 04:26, 04:56 review timestamps) before this fifth sidecar was still
auto-created at 06:40:26Z under `auto_created_by: supervisor-underutilization`.

Given Section 4 shows the parent has had zero state change since Follow-up 4,
and the blocking condition is purely a pending human decision (not something
another acceptance/dependency-map sidecar can move), further
`acceptance_packet` sidecars on this parent are unlikely to add value until
either:

- the `Human/Ops` decision in Section 5 lands, or
- the parent owner records new evidence or a materially different blocker.

Recommendation to the chair/orchestrator: continue treating this parent as
sidecar-saturated and prefer routing idle capacity to unowned or unblocked
work over auto-creating additional acceptance-packet sidecars here.

---

## 7. Non-Claims

This support packet does not:

- approve the parent repair;
- certify live dev runtime health;
- change RuntimeBinding ownership or queue semantics;
- authorize live broker or real-funds side effects;
- change supervisor cadence, dispatch policy, or canonical architecture;
- replace before/after evidence captured by the parent owner;
- make the `Human/Ops` decision on the parent's behalf, or recommend bypassing
  that gate through automation.

---

## 8. Handoff To Reviewer

**To:** `Claude`
**From:** `Claude2`
**Requested review outcome:** approve this follow-up if the currency
re-check, the live-store parent-status read, and the E2E-R1-grounded decision
brief in Section 5 are accurate.

Recommended reviewer use:

1. Treat Section 3 as proof the cited facts and tests are still current.
2. Treat Section 4 as confirmation the parent is unchanged since Follow-up 4
   — still `blocked`, still `waiting_for: Human/Ops`.
3. Treat Section 5 as the first decision-ready brief for the actual blocker,
   to hand to whoever holds `Human/Ops` — not as a technical acceptance gate.
4. Treat Section 6 as a process flag for the chair/orchestrator, not a
   requirement on the parent owner.
5. Do not treat this sidecar approval as parent repair approval, or as
   approval of Option A or Option B in Section 5.
