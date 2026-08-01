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

No live container was started or restarted during this review or owner
finalization. No projection state, generation directory, or volume data was
modified or deleted. The PR only changes Compose configuration and its
regression test.

The retention default of four is intentionally only a configuration change in
this hotfix. Existing projector maintenance code will prune generations beyond
the configured retention when the projector next runs. Therefore this packet
does not authorize restarting the stopped legacy projector or performing
manual state cleanup. The source change is merged, but operational rollout
remains a separately governed and currently blocked action.

## Merge and delivery disposition

Antigravity independently approved the original implementation head
`85e835448f7b86ce77ad9e4e0cc80961879b29c0`. Strict up-to-date branch
protection then required the merge-only composed head
`c3bb0fe5e23e9ed2c8e334c214050f2dd2229faa`, whose parents are that approved
implementation and `dev` commit `76bbb04b569331a81916330d1cf713d068527c89`.
The two implementation files are byte-identical between the approved head and
the composed head.

After the composed-head gate passed, PR #4448 merged into `dev` at
2026-08-01T15:16:58Z as
`d2a9a6079789b6da1f15978ff7310c22a129f379`. The merge commit is an ancestor
of the current `origin/dev`, and all reported PR checks, including `Pantheon
canonical review gate` and `Pantheon root merge freeze 2026-07-27`, are
successful.

No `Pantheon Nonprod Deploy` run targets the hotfix merge commit. The latest
recorded dev deployment attempts predate the merge and failed on 2026-07-31.
Therefore source delivery is complete but live rollout is not claimed. The
stopped legacy projector must remain stopped until a separately governed safe
rollout; projection state and retained generations must not be deleted.

## Final owner verification

At 2026-08-01T16:34:21Z, `Codex2` reran the same focused owner checks on the
merged task branch: 25 lifecycle projector/Compose tests, 3 BFF readiness
tests, and 3 focused BFF read-model tests passed; `docker compose -f
docker-compose.yml config --quiet` also passed. These checks are closeout
verification and do not replace Antigravity's independent verdict in
`evidence.json`.
