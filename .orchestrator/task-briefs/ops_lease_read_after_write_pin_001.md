# OPS-LEASE-READ-AFTER-WRITE-PIN-001

## Why this corrective exists

Two unreviewed changes were merged into `dev`:

- PR #3754 / merge `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524`
  added a sound controller mechanism that retries only when GitHub serves the
  exact expired predecessor blob replaced by the successful lease CAS.
- PR #3755 / merge `ea9f35924abbeee07e400d0cd493a25e39f58aa3`
  added a broad YAML loop that retries every `verify` failure up to 50 times.

The workflow still pins controller commit
`65a1d653222ab378c994df6c40349139cd429831` and the old controller checksum, so
the selective PR #3754 mechanism is not used. The broad PR #3755 loop can
delay real ownership/auth/parse failures and its test only asserts YAML text.

This is a fleet implementation task. Start from current `origin/dev`. Preserve
both PRs as incident evidence and deliver a corrective PR; do not amend or
rewrite either merged history.

## Required implementation

1. Remove the broad `for attempt in $(seq 1 50)` verification loop introduced
   by PR #3755.
2. Pin `.lease-controller` to an exact protected Pantheon merge commit that
   contains PR #3754's selective `previousContentSha` implementation. Update
   the pinned SHA and controller checksum together and verify both before use.
3. Only the immediate post-acquire verify may opt in with bounded
   `--initial-visibility-wait-seconds` and
   `--initial-visibility-poll-seconds`. Heartbeat, deploy-step guard, release,
   and every other verify remain strict with no visibility retry.
4. The retry must occur only for the exact predecessor content SHA recorded by
   the successful CAS when that predecessor was expired before acquisition.
   A foreign active lease, different blob SHA, missing/malformed predecessor,
   auth failure, API error, malformed payload, heartbeat death, or timeout
   must fail closed.
5. Do not dispatch a Pantheon proof from this task.

## Mandatory validation

- Controller unit tests must prove exact-expired-predecessor then current
  succeeds; foreign active replacement fails on first read; wrong predecessor
  SHA fails on first read; missing opt-in fails on first read; bounded timeout
  fails; invalid wait/poll bounds fail.
- Workflow contract tests must prove the exact controller commit and checksum
  match the pinned file and that only the initial verify carries the two opt-in
  flags.
- A negative workflow test must prove the PR #3755 broad shell loop is absent.
- Existing heartbeat identity, deploy-step lease guard, release, shared
  workflow state, and no-cross-cancel tests must remain green.
- Run `git diff --check` and validate the workflow YAML.

## Delivery and review

- Owner: Codex2
- Reviewer: Claude
- Target: `ajoe734/pantheon` `dev`
- Auto-merge must remain disabled.
- Compose current `origin/dev` before the final test run.
- Claude must inspect and name the exact final head in a governed approval
  before merge.
- `OPS-DEPLOY-WORKFLOW-GUARD-001` may rerun Pantheon only after this task and
  `OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001` have both been reviewed and
  merged.

## Unreviewed merge handling

PR #3757 merged as `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` at
2026-07-16T14:10:04Z without any GitHub or governed Claude review; its commit
still says `Reviewer: pending`. Do not treat that merge as task completion and
do not dispatch a deploy proof from it.

Claude must independently audit that exact merge and rerun every named
controller and workflow negative test. If any defect is found, return the task
to owner Codex2 for a corrective PR; the reviewer must not implement the fix.
Even if the code is clean, preserve the premature merge as a process failure
in the task evidence.
