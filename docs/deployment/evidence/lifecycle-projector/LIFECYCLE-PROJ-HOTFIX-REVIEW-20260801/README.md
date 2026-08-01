# Lifecycle projector emergency containment review

Task: `LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801`

This packet is the task-scoped handoff for independent review of Pantheon PR
[#4448](https://github.com/ajoe734/pantheon/pull/4448). The only eligible
independent reviewer currently assigned by canonical task state is
`Antigravity`. `Codex` authored the reviewed hotfix, and `Codex2` is the same
canonical identity, so neither may supply the independent verdict.

## Exact review target

- Repository: `ajoe734/pantheon`
- PR: `#4448`
- Base: `dev`
- Head branch: `hotfix/lifecycle-projector-memory-guard-20260801`
- Required head: `85e835448f7b86ce77ad9e4e0cc80961879b29c0`
- Observed head at 2026-08-01T14:41:22Z: exact match
- Changed paths: `docker-compose.yml` and
  `services/trade_journey/test_lifecycle_projector_compose.py`

## Codex author revalidation

Codex revalidated the immutable PR head in a detached worktree. These results
are supporting evidence, not independent review approval.

- 25 focused lifecycle projector and Compose tests passed.
- 3 BFF projector-readiness tests passed. They prove readiness becomes 503
  with explicit `stale` or `error` projector truth and recovers only after a
  fresh successful poll.
- 3 focused BFF read-model tests passed. They prove the committed projector
  wrappers remain readable from the configured stores and degraded projector
  truth cannot reuse historic live acceptance.
- `docker compose -f docker-compose.yml config --quiet` passed.
- `git diff --check origin/dev...HEAD` passed in the exact-head worktree.
- Expanded Compose defaults are `restart: no`, memory limit
  `17179869184` bytes (16 GiB), and generation retention `4`.
- The BFF and projector mount the same `bff-data` volume. The BFF reads the
  two files under `lifecycle-projection/current/`, while its Compose dependency
  on the projector is reduced from `service_healthy` to `service_started`.
- The BFF-only deployment path still uses `--no-deps operator-bff`; the
  focused Compose test proves that path does not rebuild or restart the
  projector.

The exact command transcript is in
[`codex-author-revalidation.log`](./codex-author-revalidation.log). Machine-
readable task state is in [`evidence.json`](./evidence.json).

The owner closeout disposition, including the post-approval head change and
the explicit rollout blocker, is recorded in
[`owner-closeout.md`](./owner-closeout.md). This does not replace or expand
Antigravity's exact-head verdict in `evidence.json`.

## Data and rollout boundary

No live container was started or restarted during this review. No projection
state, generation directory, or volume data was modified or deleted. The PR
only changes Compose configuration and its regression test.

The retention default of four is intentionally only a configuration change in
this hotfix. Existing projector maintenance code will prune generations beyond
the configured retention when the projector next runs. Therefore the reviewer
must not treat this packet as authorization to restart the stopped unbounded
projector or to perform manual state cleanup. Dev rollout remains pending the
independent verdict, merge, and a separately governed rollout decision.

## Current merge gate

At 2026-08-01T14:41:22Z GitHub reported the PR `OPEN`, `MERGEABLE`, and
`BEHIND`, with no review and no auto-merge request. The head is one commit
ahead of its merge base, and current `origin/dev` is one non-conflicting task-
brief commit ahead of the same merge base.

The `dev` protection rule has strict up-to-date checking enabled. The task also
requires that PR #4448 be reviewed and merged without changing
`85e835448f7b86ce77ad9e4e0cc80961879b29c0`. Updating the hotfix branch would
change that head, so there is currently no ordinary policy-compliant merge
operation satisfying both constraints. In addition to independent exact-head
approval, Human/Ops must resolve the strict-base/exact-head conflict and supply
the required `Pantheon root merge freeze 2026-07-27` status. No bypass or head
rewrite is authorized by this packet.

## Independent reviewer completion checklist

1. Fetch PR #4448 and independently confirm the head is exactly
   `85e835448f7b86ce77ad9e4e0cc80961879b29c0`.
2. Independently rerun the focused tests and Compose validation; do not rely on
   the author-side pass as the review decision.
3. Record an explicit accept or reject verdict in `evidence.json` and commit
   that reviewed manifest.
4. If accepted, bind approval to PR #4448 and the exact head through the
   governed status command. Do not enable auto-merge and do not update the
   hotfix head.
5. Request the Human/Ops root-freeze and strict-base disposition. Merge only
   through the exact-head integrator after all gates permit it.
6. Record the merge commit and dev rollout result, or the explicit rollout
   blocker. Do not restart the stopped projector on the unbounded
   implementation and do not delete state or generations.
