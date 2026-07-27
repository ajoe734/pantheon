# SUP-COMMAND-RUNTIME-REFRESH-001 — final runtime proof

Task: Refresh installed supervisor command runtime safely
Owner: Codex · Reviewer: Codex2 · Phase: Supervisor Runtime Delivery
Runtime proof status: **complete; final exact-head review and merge pending**

This document replaces the earlier partial install/blocker narrative. The live
handoff, rollback, and roll-forward were executed on 2026-07-27. The task must
remain non-terminal until Codex2 independently reviews the final evidence PR
head, records the reviewed-head binding, and the reviewed commit merges to
`dev`.

## 1. Scope and invariant

The live supervisor config was reused byte for byte. Its sha256 before install,
after the first handoff, after rollback, and after roll-forward was:

`adab474b01b99630041cb06d565ae9dbfd7d52badc1d9e612b7cb8d4129de77e`

No `sync-dev-root.sh`, `provision_live_supervisor_config.py`, or
`check_config_drift.py --fix` path ran. No canonical task board file was edited
by hand. Every restart was declared with the PID-bound watchdog intent and
serialized by the runtime admission lock.

## 2. Accepted candidate

| Field | Exact value |
|---|---|
| Bootstrap PR | `#4254` |
| Reviewed bootstrap head | `5d1f069dc902c0692310879c02fedad4bc131b68` |
| Merge commit / installed candidate | `29054ab270d552a56ed071cedf3f45150e948b6a` |
| Candidate tree | `a8d7dcc6fa23ad02e1e62f1f8eb13cfc73cce466` |
| Merged at | `2026-07-27T15:56:33Z` |
| Branch CI run | `30282382789` |
| Orchestrator Sync run | `30282382774` |

The merge commit was the exact `origin/dev` tip selected for installation.
`Commit trailers`, `Runtime mirror guard`, `Python packaging provision`, and
`Smoke acceptance` all completed successfully on that merge commit. `Forward
to orchestrator` also succeeded. The candidate contains the previously merged
supervisor truth repair (`squash_merged_delivery_metadata` and
`merged_owner_delivery_evidence`) plus the structured `REVIEW_PR` /
`REVIEW_HEAD_SHA` approval binding introduced by PR #4254.

`dev` advanced concurrently after selection. That does not alter the installed
identity: `29054ab…` remains an accepted `dev` ancestor with its own successful
required checks.

## 3. Installation

The prior running and issued command root was:

`/home/lupin/pantheon-ci-deploy/dev-root-6692d51c9bc5`

at:

- HEAD `6692d51c9bc5a48ffcbaac8cf817b635351a7c9a`
- tree `fa55d946a09b8311de7b37e4348405267378fecd`

It was left in place as the rollback root because all in-flight workers were
issued that exact path and SHA. Mutating it in place would have invalidated
their governed status-command bindings.

The accepted candidate was installed into a new isolated root:

`/home/lupin/pantheon-ci-deploy/dev-root-29054ab270d5`

The new root was cloned from `https://github.com/ajoe734/pantheon.git`, checked
out detached at `29054ab270d552a56ed071cedf3f45150e948b6a`, and initially clean.
Its HEAD and tree exactly matched the candidate. After healthy supervisor
cycles, the runtime generated three untracked task briefs in that root. Those
runtime-state artifacts do not alter the checked-out commit or tree and are not
part of the command code identity.

## 4. Serialized handoff

`handoff/swap-supervisor.sh` performed discovery before each mutation and
required exactly one Python supervisor process. It then:

1. recorded a durable intentional-restart target for the exact old PID;
2. acquired the runtime admission lock before `TERM`;
3. required the old PID to exit and never escalated to `KILL`;
4. released the lock before launching the replacement; and
5. launched with inherited worker/runtime identity variables removed.

The first handoff was:

| Time | Old | New |
|---|---|---|
| `2026-07-27T16:00:23Z` | PID `4138635`, root `dev-root-6692d51c9bc5` | PID `395813`, root `dev-root-29054ab270d5` |

PID `395813` completed a healthy cycle at `16:02:57Z` and a reconciliation
cycle at `16:05:46Z`. `raw/post-final-refresh-snapshot.json` proves that it was
the sole supervisor, the authoritative shadow was `ok=true` and
`caught_up=true`, and the independent journal projection equaled the canonical
board.

## 5. Lease, queue, and projection preservation

The pre-cutover snapshot at `15:59:50Z` recorded four live workers, all issued
the prior `6692d51…` runtime, with leases through `16:08:38Z`.

After the reconciled candidate cycle:

- three still-running pre-cutover workers were alive and retained their issued
  `6692d51…` command root;
- their worker leases extended to `16:15:46Z`;
- their queue records retained the same `lease_owner` and extended to
  `16:35:46Z`;
- the fourth worker had completed naturally and its queue record was
  reconciled terminal rather than duplicated;
- no task had two active workers;
- the independent projection and board both hashed
  `a43281c07a66419e5a6bf4e4fbea8ad67893e72c0b7698815cf1a04508f3f30c`.

Preserved workers intentionally retain the runtime they were issued. Workers
dispatched during the temporary rollback likewise retain the rollback root
after roll-forward. This is the governed binding contract, not runtime drift.

## 6. Executed rollback and roll-forward

Rollback was executed, not merely documented:

| Step | Old PID/root | New PID/root | Healthy cycle |
|---|---|---|---|
| Rollback | `395813` / `29054ab…` | `466881` / `6692d51…` | `2026-07-27T16:08:25Z` |
| Roll-forward | `466881` / `6692d51…` | `500973` / `29054ab…` | `2026-07-27T16:11:21Z` |

The rollback snapshot proves:

- exactly one supervisor at the prior root;
- three live pre-existing workers with extended leases to `16:18:25Z`;
- queue-owner parity;
- authoritative shadow `caught_up=true` with equal hashes;
- independent projection and canonical board both at
  `a43281c07a66419e5a6bf4e4fbea8ad67893e72c0b7698815cf1a04508f3f30c`;
- unchanged config hash.

The final roll-forward snapshot proves:

- exactly one supervisor, PID `500973`, at `dev-root-29054ab270d5`;
- lifecycle `running` and heartbeat `2026-07-27T16:11:21Z`;
- all four active worker PIDs alive, with leases through `16:21:21Z`;
- every started queue record bound to its live `lease_owner`, with leases
  through `16:41:21Z`;
- no duplicate active dispatch;
- independent projection and board both at
  `4848c1e04ff07357fe71411de3b455d1d412ead19703995118be43cf940c3276`;
- authoritative shadow `ok=true`, `caught_up=true`, and equal projected /
  expected hashes;
- unchanged config hash and exact candidate HEAD/tree.

## 7. Focused validation

The owner ran:

- PR #4254 exact-head and merge-state inspection with `gh pr view` /
  `gh pr checks`;
- merge-commit check inspection with the GitHub check-runs API;
- candidate ancestry and supervisor truth-symbol verification;
- `python3 -m py_compile handoff/runtime-snapshot.py`;
- three `swap-supervisor.sh --discover-only` probes;
- three serialized live swaps: refresh, rollback, roll-forward;
- a healthy supervisor cycle after every swap;
- pre-refresh, post-refresh, rollback, and final roll-forward snapshots;
- exact HEAD/tree, remote, config sha256, worker lease, queue owner, and
  authoritative projection checks;
- `git diff --check` and evidence JSON parsing before final publication.

Raw evidence is under `raw/`. The earlier
`raw/installed-root-verification.txt`, `raw/pre-refresh-snapshot.json`, and
`raw/post-install-snapshot.json` are retained only as historical stage-1
context; they no longer define the acceptance result.

## 8. Acceptance mapping

| Acceptance | Result |
|---|---|
| Exact accepted `dev` candidate with required checks and supervisor truth repair | **pass** |
| Installed command root HEAD/tree exactly match candidate; config byte-identical | **pass** |
| Serialized refresh preserves live worker leases and prevents duplicate dispatch | **pass** |
| PID replacement and command-runtime handoff without manual board edits | **pass** |
| Authoritative shadow caught up; projection and queue/worker parity pass | **pass** |
| Rollback to prior runtime tested and roll-forward restored candidate | **pass** |
| Evidence PR exact-head Codex2 review, merge, archive, and `done` | **pending final review** |

## 9. Residual risks and final gate

- The unchanged live config still names the rollback root in
  `watchdog.supervisor_command`. A watchdog-driven future relaunch therefore
  fails safely back to accepted `6692d51…`, not an unknown runtime. Making the
  candidate the persistent watchdog target requires a separate config-changing
  task after all old-runtime worker pins drain.
- The candidate root contains supervisor-generated untracked task briefs after
  its healthy cycles. Its git HEAD and tree remain exact. Those files must not
  be committed as product source or confused with runtime code.
- Serial provider probes made each healthy cycle take roughly two minutes.
  Lease renewal remained within the 600-second worker window throughout.

The remaining gate is independent Codex2 review of the exact PR #4257 head.
The reviewer must bind `REVIEW_PR=4257` and the full `REVIEW_HEAD_SHA`, record
this manifest as `REVIEW_FILE`, and approve only that head. The owner may run
`done` only after the reviewed PR is merged into `dev`.
