# MGMT-PERF-IA-002 BFF And Frontend Handoff Follow-up 4

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This packet is support material only. It does not change canonical contracts,
BFF runtime or tests, ranking formulas, governance behavior, registry state,
or `execute-plans`. The parent owner decides whether to absorb it.

## 1. Material Delta Since Follow-up 3

Follow-up 3 is merged into `dev` through PR `#3133` at merge commit
`df26b5c1a`. The parent task is now explicitly `blocked`, waiting for
`Human/Ops`, because its task branch ancestry differs from current `origin/dev`
across 48 unrelated files and focused tests cannot collect after deletion of
`persona_allocation_policy.py`.

At inspection time `origin/task/MGMT-PERF-IA-002` still points to candidate
commit `d0d4d0497`. That commit itself removes six lines from
`services/control-plane/bff/main.py`; the branch-wide diff is not a safe proxy
for parent-owned work. Candidate branch observations remain unmerged evidence.

The parent status prescribes the recovery boundary: create a clean task
branch/worktree from current `dev` and replay only scoped `main.py` changes from
`eed5e38df` and `d0d4d0497`. This sidecar does not perform that replay.

## 2. BFF Recovery Handoff

The recovered parent branch should make its ownership reviewable without
depending on contaminated ancestry:

1. Start from current `origin/dev`, not the existing parent merge base.
2. Inspect the patches for `eed5e38df` and `d0d4d0497`; replay only intentional
   performance/ranking `main.py` hunks. Do not replay commits wholesale when
   that would import unrelated paths.
3. Confirm `git diff --name-only origin/dev...HEAD` contains only parent-owned
   BFF contract/test files. Any deletion of allocation, containment, persona,
   or unrelated test surfaces is a recovery failure.
4. Re-run the focused contract test only after collection succeeds against the
   clean base.
5. Preserve the query-envelope acceptance from the earlier packets: effective
   filters, resolved snapshot, deterministic cohort/order, backend-authored
   rank, explicit source state, and evidence/review/apply separation.
6. If the replay does not actually implement filter effectiveness, historical
   snapshots, pagination continuity, or receipt loopback, keep those items as
   explicit gaps or assign named follow-ups. Do not convert test collection
   success into a broader contract claim.

Suggested recovery evidence:

```bash
git fetch origin dev task/MGMT-PERF-IA-002
git show --stat --oneline eed5e38df
git show --stat --oneline d0d4d0497
git diff --name-status origin/dev...HEAD
git diff --check -- services/control-plane/bff
pytest -q services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py
```

Commands are guidance for the parent-owned clean worktree. The parent must use
the repository task-branch and worker-commit workflow rather than mutating the
shared/live checkout.

## 3. Query And Frontend Truth During Recovery

Until a clean parent PR merges, frontend owners should consume current `dev`
truth and treat the candidate query contract as unavailable for implementation
claims.

| Concern | Safe handoff rule |
|---|---|
| Common filters | Preserve persona/runtime/strategy/pool/sleeve/artifact/broker/stage/period/quarter/`asOf` in frontend route context, but do not claim every current endpoint applies or echoes every filter. |
| Historical snapshot | Unknown `asOf` must not silently fall back to latest. If current `dev` lacks the contract, render unsupported/unavailable. |
| Official rank | Display only backend-authored rank, formula/version, eligibility, and exclusion reason. Client sorting must not renumber rows. |
| Source confidence | Keep formal/partial/fallback/degraded/unavailable, freshness, coverage, and missing bindings visible. Missing metrics remain `null`, not zero. |
| Recommendation | Keep ranking evidence, recommendation, Human Review, governed apply, and receipt as distinct states. |
| Actions | No direct service, registry, allocation, runtime, or broker mutation fallback while governed links are absent. |
| Compatibility | Do not publish frontend types or acceptance examples from the contaminated branch. Generate them only from the merged clean contract. |

## 4. Operator Journey While Parent Is Blocked

1. Operator opens Performance or Rankings with visible filter context.
2. Frontend calls only routes and fields available on merged `dev`.
3. Unsupported effective-query or historical-snapshot behavior is shown as an
   explicit unavailable state; the browser does not fabricate continuity.
4. Official rank and confidence remain backend-authored. Presentation sorting
   cannot change rank labels or rank excluded/null-metric rows.
5. Recommendation navigation stops at the last governed state evidenced by
   merged BFF responses. It does not imply approval or applied effect.
6. After the clean parent PR merges, the frontend owner revalidates the exact
   response examples and negative cases before wiring the three centers.

## 5. Parent Absorption Checklist

- [ ] Clean recovery branch starts from current `dev`.
- [ ] Only intentional parent-owned hunks from `eed5e38df` and `d0d4d0497`
      are replayed.
- [ ] Branch diff contains no unrelated deletions or subsystem changes.
- [ ] Focused tests collect and pass from the clean branch.
- [ ] Tests distinguish request acceptance from actual filter effectiveness.
- [ ] Supported, empty, unsupported, known/unknown/malformed snapshot, tied,
      excluded, null-metric, degraded, and paginated cases are honestly scoped.
- [ ] Frontend handoff examples are regenerated from the merged clean contract,
      not the contaminated candidate branch.
- [ ] Deferred Human Review/apply/receipt work has a named owner and exposes no
      inferred action capability.

## 6. Sidecar Verification And Reviewer Handoff

```bash
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
```

Reviewer `Antigravity` should verify that the blocker and recovery instructions
match parent status, candidate observations are not described as merged truth,
and frontend guidance remains fail-closed. After review, the parent owner may
selectively absorb the packet. Sidecar approval does not approve the parent
runtime implementation or authorize live-capital operations.
