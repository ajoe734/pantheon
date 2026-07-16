# OPS-DEPLOY-WORKFLOW-GUARD-001 deploy proof attempt

Captured: 2026-07-16T13:30:51Z

## Dispatches

Both proof runs were dispatched at `2026-07-16T13:03:45Z` with no workflow
disable and no run cancellation.

| repo | workflow | run | ref / candidate | result |
| --- | --- | --- | --- | --- |
| `ajoe734/pantheon` | `Pantheon Nonprod Deploy` (`269991390`) | `29500642280` | workflow ref `dev`, target ref `0a97b57f7f9d90aca7b940df67a5016494700bc0` | `failure` |
| `ajoe734/execute-plans` | `Pantheon Dev FE Deploy` (`292028803`) | `29500642299` | candidate `62a9bb03b17037b40153ccb1a150974cf188779b`, gate run `29497751979` | `success` |

## What this proves

- The shared workflows stayed `active` before, during, and after the proof
  attempt.
- The execute-plans proof run initially queued behind an already-running
  workflow_dispatch run (`29499287883`) and then ran after that run completed;
  it was not cancelled.
- The task-owned execute-plans read-only deploy completed successfully at
  `2026-07-16T13:18:32Z`.
- The task-owned Pantheon deploy acquired the shared dev environment lease,
  started the identity-bound heartbeat, and released the lease cleanup path
  after failure.
- No `gh workflow disable`, `gh run cancel`, list cancellation, or force-cancel
  was used during this proof attempt.

## Remaining failure

The Pantheon proof did not reach terminal success. Run `29500642280` failed at
`2026-07-16T13:28:02Z` in step `Deploy dev VM stack under lease`.

Relevant job facts:

- `Acquire shared dev environment lease`: success
- `Start identity-bound lease heartbeat`: success
- `Deploy dev VM stack under lease`: failure
- `Stop heartbeat and release only after complete success`: success
- final process exit: `75`

The failed log shows the deploy entered the VM through `gcloud compute ssh`,
ran the remote deploy path for ~23 minutes, then returned through
`googlecloudsdk/surface/compute/ssh.py` with `SystemExit: 1` and
`Process completed with exit code 75`.

This is a dev deploy-path failure, not evidence that a worker disabled the
shared workflow or cancelled another run. Acceptance remains incomplete because
the Pantheon proof still needs a terminal-success run.

## Current live state

At capture time:

| item | state |
| --- | --- |
| `ajoe734/pantheon` workflow `269991390` | `active` |
| `ajoe734/execute-plans` workflow `292028803` | `active` |
| stale Pantheon run `29469158508` | still `queued` |
| prior execute-plans run `29499287883` | completed `failure`, not cancelled by this task |
| task execute-plans run `29500642299` | completed `success` |
| task Pantheon run `29500642280` | completed `failure` |

Next required action: fix or clear the Pantheon dev deploy-path failure, then
rerun the Pantheon proof to terminal success while preserving the no
disable/cancel rule.

Publication note: PR publication may require a follow-up evidence-only push if
branch protection reports a stale push check from unrelated dev history merged
into the task branch.
