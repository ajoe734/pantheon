# MGMT-PERF-IA-002 BFF And Frontend Handoff Follow-up 5

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This is support material for parent-owner absorption. It changes no canonical
contract, BFF runtime or test, ranking formula, governance behavior, registry
state, or frontend source.

## 1. Evidence Checkpoint

Follow-up 4 merged into `dev` through PR `#3134` at merge commit `a82f1e844`.
At inspection time `origin/task/MGMT-PERF-IA-002` still points to candidate
commit `d0d4d0497`; no newer parent commit is available to demonstrate clean
recovery, effective filtering, historical snapshot selection, or receipt
loopback. Candidate-branch behavior therefore remains unmerged evidence.

The actionable handoff is unchanged: recover the parent work on a clean branch
from current `dev`, replay only parent-owned performance/ranking hunks, and
regenerate response examples from the merged result. This sidecar does not
perform or approve that replay.

## 2. Recovery Acceptance Packet For The BFF Owner

The clean parent PR should include a compact evidence table for each supported
read family: Performance Attribution, Persona League, Quarterly Ranking, and
Quarterly Recommendations.

| Proof | Minimum evidence |
|---|---|
| Scope isolation | `origin/dev...HEAD` lists only declared parent-owned BFF contract/test paths and no unrelated deletions. |
| Effective filters | Matching, non-matching, and unsupported values demonstrate selection or explicit rejection for every claimed filter. |
| Snapshot truth | Latest, known historical, unknown historical, and malformed `asOf` are distinguishable; latest is never relabeled. |
| Stable cohort | List, pagination, drilldown, ranking evidence, and recommendation preserve stable identity, cohort, order, and snapshot. |
| Official rank | Rank, eligibility, exclusion, formula/version, ties, and null/non-finite metrics remain backend-authored and deterministic. |
| Source state | Confidence, freshness, coverage, observed time, and diagnostics preserve partial/degraded/unavailable truth. |
| Governance boundary | Recommendation, Human Review, apply availability, operation, and receipt are separate states; absent links fail closed. |

Request acceptance or top-level field presence alone is not evidence that a
filter or snapshot boundary is effective.

## 3. Response Examples Required Before Frontend Wiring

After the clean BFF contract merges, give the `execute-plans` owner sanitized
examples for:

1. a filtered success with recoverable effective context and resolved
   snapshot;
2. an empty filtered cohort that does not widen to all rows;
3. unsupported filter and malformed timestamp validation;
4. unavailable historical snapshot without latest-data fallback;
5. tied, excluded, null-metric, and degraded rows;
6. deterministic pagination bound to one cohort and snapshot;
7. recommendation submitted to Human Review without approval/apply claims;
8. stale or unauthorized apply rejection and, if implemented, asynchronous
   operation-to-receipt loopback.

Field names and error shapes remain parent-owned. Examples must come from the
merged implementation and must not be synthesized from the contaminated
candidate branch or from this support packet.

## 4. Operator Journey During Recovery

1. Keep persona/runtime/strategy/pool/sleeve/artifact/broker/stage/period,
   quarter, and `asOf` visible in route context.
2. Call only merged `dev` routes and render unsupported query/history behavior
   as unavailable.
3. Display backend-authored rank and source confidence; browser sorting changes
   presentation only.
4. Preserve exclusions, null metrics, degraded coverage, and stale state across
   drilldowns without client-side joins or confidence upgrades.
5. Stop at the last governed state evidenced by the BFF. No direct service,
   registry, allocation, runtime, or broker mutation fallback is permitted.
6. Re-run the example matrix after the clean parent PR merges before claiming
   the Performance, Rankings, and Governance journey is integrated.

Frontend delivery belongs in the separate `ajoe734/execute-plans` repository,
using strict live BFF mode on Pantheon-owned dev hosting. No frontend tree is
materialized by this task.

## 5. Parent Absorption And Reviewer Handoff

Parent owner `Antigravity` may absorb this packet only after deciding which
query behaviors are implemented now and which have named follow-up owners.
Reviewer `Antigravity` should verify that:

- the checkpoint matches the current merged/candidate boundary;
- all implementation claims are expressed as required proof, not completed
  behavior;
- recovery scope excludes unrelated branch ancestry;
- frontend guidance remains backend-authored and fail-closed.

Suggested sidecar verification:

```bash
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
```

Approval of this packet does not approve the parent runtime implementation and
does not authorize live-capital operations.
