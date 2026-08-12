# SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729

Provider-first helper claim when the preferred lane is busy.

- Owner: Claude · Reviewer: Antigravity · Phase: L12 runtime/fleet guardrails
- Base: `dev` @ `ab5caf7d4` · Branch: `task/SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729` · PR #4389
- Machine-readable manifest: [`evidence.json`](evidence.json)

## Gap

Claude2 was busy finalizing L12-OBS work, so the helper-claim path treated the
SUP-L12 task as claimable and handed it to idle Codex2 — purely because Codex2
sat in `owner_fallbacks`. Being busy or paused is not a reason to leave the
provider-first family.

Three helper-claim entry points built their candidate list from raw
`owner_fallbacks` / `preferred_lane_order` and never passed it through
`l12_provider_first_candidates`, the provider-first filter dev already applies
on the automatic-reassignment path.

## Change

`.orchestrator/supervisor.py` (+8/-3), all in `db21b1a75`:

| Function | Change |
| --- | --- |
| `plan_helper_claim_assignment` | Filter the assembled fallbacks provider-first before the emptiness check, so an L12 task yields no plan for a Codex-family idle agent. |
| `task_preferred_lane_blocks_helper_claim` | Keep the *declared* order for the "is any lane declared at all" test, then filter provider-first before the membership test. |
| `task_next_preferred_helper_lane` | Filter the declared order provider-first, so the paused-owner wait slot advances past a Codex lane to the next family lane instead of stalling. |

`l12_provider_first_candidates` returns its input unchanged when
`task_is_l12_recovery_work(task)` is false, so no non-L12 dispatch behaviour
moves. No `provider_permissions.py` edit and no config edit.

## Validation

**dev tip cannot import `supervisor` at all.** PR #4590's stale-base squash
`23ae23c21` deleted `provider_permissions.provider_auth_probe_due` while
`supervisor.py:88` still imports it, and left four calls to
`rewrite_provider_health.classify_probe_failure_kind` with no definition
anywhere on dev. Repairing that is PR #4599's lane, not this one, so every run
below uses PR #4599's head `eb04f8487` as the base and applies this task's
two-file diff on top.

| Run | Base | Result |
| --- | --- | --- |
| Full `test_supervisor.py` | `eb04f8487` + this diff | **613 passed, 162 subtests** |
| Full `test_supervisor.py --collect-only` | `eb04f8487`, no diff | 606 collected (606 + 7 new = 613; none removed) |
| `-k "helper_claim or preferred_helper_lane or plan_helper_claim"` | `eb04f8487` + tests only, `supervisor.py` reverted | **5 failed**, 20 passed — the gap proof |
| same selection | `eb04f8487` + this diff | **25 passed** |

Interpreter: `/home/lupin/pantheon/.venv/bin/python`, `PYTHONPATH=.orchestrator`.
Exact commands, timestamps, and the five failing test names are in
`evidence.json`.

## Superseded PR head

The former head `e2c6a52c9` was a merge of a branch stale since 2026-07-29.
Measured two-dot against dev it deleted 134 `supervisor.py` defs (539 → 405)
and 139 test methods (611 → 472) — it would have reverted large parts of dev.
It was reopened, never approved, so no review-proof tag was orphaned. The
branch was rebuilt from dev tip and force-pushed.

## Acceptance

Four of the five acceptance criteria are met outright. The fifth, *full
supervisor regression passes*, is met on `eb04f8487` + this diff and **cannot**
be demonstrated on dev tip alone until PR #4599 lands. See `evidence.json` →
`acceptance` for the per-criterion mapping.
