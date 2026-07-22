# Live incident observed 2026-07-16 ~04:26-04:40 UTC

While working this task, `ps -ef` on the shared host showed a running
process:

```
lupin  2648904  4180748  0 04:26 pts/4  00:00:00 bash /tmp/pint_deploy_guard.sh task/PINT-GATE-CLI-FIX
```

`/tmp/pint_deploy_guard.sh` (not part of this repo; a local script left by
a different worker's task, `PINT-GATE-CLI-FIX`) is a 30-minute loop that,
every 8 seconds:

- disables `ajoe734/pantheon` workflow `269991390` (`Pantheon Nonprod
  Deploy`) if it is `active`;
- disables `ajoe734/execute-plans` workflow `292028803` (`Pantheon Dev FE
  Deploy`) if it is `active`;
- cancels every non-completed run of pantheon workflow `269991390` except
  one allowlisted run id;
- cancels every non-completed run of execute-plans workflow `292028803`,
  with no exception at all;
- cancels every non-completed run of execute-plans workflow `276388492`
  (a CI integration gate, not a deploy workflow) whose head branch is not
  `task/PINT-GATE-CLI-FIX`.

This is structurally identical to the `LOOP-PROD-FE-001` guard already
recorded in the task brief (same two workflow ids, same disable-then-cancel
loop shape), confirming the brief's point 2: the pattern recurs across
unrelated tasks and is not one worker's one-off mistake. It also broadens
the blast radius one step further by cancelling unrelated CI (not just
deploy) runs on a third workflow.

`gh api` confirmed both shared deploy workflows were `disabled_manually`
for the entire span this task was worked (04:27 through at least 04:40
UTC):

```
$ gh api repos/ajoe734/pantheon/actions/workflows/269991390 --jq '{name, state}'
{"name":"Pantheon Nonprod Deploy","state":"disabled_manually"}
$ gh api repos/ajoe734/execute-plans/actions/workflows/292028803 --jq '{name, state}'
{"name":"Pantheon Dev FE Deploy","state":"disabled_manually"}
```

No action was taken against this process or the workflows from this task:
it is owned by a different task/worker, and killing another lane's live
process without coordination would be its own uncoordinated action. This
is flagged here for Human/Ops as a second, independent live instance of the
exact defect `OPS-DEPLOY-WORKFLOW-GUARD-001` exists to close, on top of the
already-paused `LOOP-PROD-FE-001`.
