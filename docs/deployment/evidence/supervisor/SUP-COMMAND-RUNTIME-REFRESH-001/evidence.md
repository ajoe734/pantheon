# SUP-COMMAND-RUNTIME-REFRESH-001 — governed command runtime refresh

Task: Refresh governed supervisor command runtime without config changes
Owner: Claude · Reviewer: Codex2 · Phase: Supervisor Runtime Delivery
Status of this document: **partial delivery — stage 1 (install) complete and
verified; stage 2 (supervisor process handoff) is blocked on a permission the
background worker does not hold.** See §6.

Scope rule honoured throughout: **the live supervisor config is not edited.**
Its sha256 is identical before and after every action recorded here, and no
`provision_live_supervisor_config.py` / `sync-dev-root.sh` path was run (either
would have rewritten the live config as a side effect).

## 1. Candidate

| | |
|---|---|
| Candidate commit | `6578ef968f7e0374b046de0afb5b07ce2f81558e` |
| Candidate tree | `55680b29ccb50ab5c1771ddd9f10590f1ba774c9` |
| Source | exact `origin/dev` tip at 2026-07-26T22:5xZ |
| Delivery it carries | PR #4223 (`SUP-WORKER-TRUTH-RECONCILE-001`), merged into `dev` 2026-07-26T22:09:29Z |
| Required checks (`dev` protection: Commit trailers, Runtime mirror guard, Smoke acceptance) | all `SUCCESS` on the candidate |
| Non-required failure on the same SHA | scheduled `Hourly Publish Cut` run 30222860390 failed its own `Check if dev advanced` step; it is not a required context and does not gate the runtime |
| Supervisor truth repair present in candidate | yes — `merged_owner_delivery_evidence` / `squash_merged_delivery_metadata` are in `6578ef968:.orchestrator/supervisor.py` |
| Candidate smoke, run on the candidate tree | `test_supervisor.{OwnerlessInProgressReconciliation,MergedDeliveryEvidence,SquashMergedDeliveryEvidence,MergedPullRequestLookup,WorkerDeliveryIdentity}Tests` → **55 tests, OK** |

Full transcript: `raw/installed-root-verification.txt` §1–§2.

## 2. Why the candidate was installed into `dev-root-d054bd49cb48`

The live config contains exactly one command-root reference:

```json
"watchdog": { "supervisor_command": [
  "/usr/bin/python3.12", "-u",
  "/home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48/.orchestrator/supervisor.py",
  "--config", "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json",
  "--verbose" ] }
```

There is no `command_root` config key: `common.status_command_runtime_env`
derives `PANTHEON_COMMAND_ROOT` from `ROOT` — the directory of the *running*
supervisor. At the start of this task those two disagreed:

| | path | HEAD |
|---|---|---|
| running supervisor (pid 901543, since 15:05:13) | `dev-root-bdbd0a99bf68` | `bdbd0a99bf68…` |
| root named by the live config's watchdog command | `dev-root-d054bd49cb48` | `d054bd49cb48…` (PR #4189, older than the running code) |

A `runtime/live-supervisor-mainroot-config.json.bak-ag-claude-first` backup from
13:10 the same day still names `dev-root-bdbd0a99bf68`, so the live config was
re-pointed at the older root at 15:04 and the supervisor was then launched by
hand from `bdbd0a99bf68` at 15:05. That left a latent downgrade trap: any
watchdog relaunch would have started **older** code than what was running.

Installing the candidate into the root the config already names satisfies the
brief without touching config, and closes that trap in the same move:

- config is reused byte for byte — no edit, no re-provision;
- the config-named watchdog path now holds the accepted runtime;
- the running root `bdbd0a99bf68` is left **completely untouched**, which is a
  hard requirement, not a convenience: all six live worker records pin
  `status_command_runtime.command_root=…/dev-root-bdbd0a99bf68` with
  `source_sha=bdbd0a99bf68…`, and `validate_status_command_runtime_binding`
  re-reads `git rev-parse HEAD` of that root on every governed status command.
  Refreshing the *running* root in place would have broken every in-flight
  worker's ability to write canonical status;
- rollback is a checkout of one recorded SHA in one root, with the entire prior
  runtime still present on disk (§5).

Cost of this choice, recorded deliberately: the directory name
`dev-root-d054bd49cb48` no longer equals its HEAD. Every other `dev-root-<sha>`
in the deploy area still does. The name cannot be corrected without editing the
live config, which this task forbids.

## 3. Stage 1 — installed, verified

```
before: HEAD d054bd49cb485f091e3fb31b1d91e57d4fe372ab  tree 72618947f7f3…  clean
        (no process had this root as cwd — full /proc cwd scan)
action: git -C …/dev-root-d054bd49cb48 checkout --detach 6578ef968f7e…
        (checkout, not `reset --hard`: it refuses to clobber a dirty tree)
after:  HEAD 6578ef968f7e0374b046de0afb5b07ce2f81558e  tree 55680b29ccb5…  clean
        remote https://github.com/ajoe734/pantheon.git
        merge-base --is-ancestor HEAD origin/dev → exit 0
        .orchestrator/bin/{agy,claude,codex,copilot,gemini,gemini2,gh} present
```

Installed tree equals the candidate tree exactly, and the installed root is
clean, so the runtime is the candidate and nothing else.

Live config sha256, before and after:
`cd98e7c5c259b7eb3fe5bda23afddb40fd04381f86a1525fa8a7f46b1eddaa54` — identical.

The runtime that root will hand to workers, computed from the installed root and
the unchanged live config (`raw/installed-root-verification.txt` §7):

```
PANTHEON_COMMAND_ROOT        /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48
PANTHEON_COMMAND_RUNTIME_SHA 6578ef968f7e0374b046de0afb5b07ce2f81558e
PANTHEON_COMMAND_REMOTE      ajoe734/pantheon
PANTHEON_COMMAND_BASE_REF    origin/dev
PANTHEON_TASK_STATE_STORE_MODE authoritative
PANTHEON_TASK_STATE_EVENT_LOG  /home/lupin/pantheon-ci-deploy/runtime/task-state-events.jsonl
```

## 4. Runtime state captured around stage 1

`raw/pre-refresh-snapshot.json` (2026-07-26T22:51:02Z) and
`raw/post-install-snapshot.json` (2026-07-26T23:03:56Z), both produced by
`handoff/runtime-snapshot.py`, which only reads. They record the live config
hash, every deploy root's HEAD/tree/dirtiness, supervisor processes and cwd,
`state.json` supervisor lifecycle and `task_state_shadow`, every active worker
with its lease and issued command runtime, queue records with their leases,
worktree leases, and an independent projection of the authoritative journal.

Across the install: config hash unchanged, `dev-root-bdbd0a99bf68` unchanged
(`bdbd0a99bf68…`, same 22 untracked/modified generated task-brief entries),
supervisor still pid 901543 from `bdbd0a99bf68`, all six worker leases intact
and still `bdbd0a99bf68`-pinned, `worker_lease_seconds=600` leases extended to
`2026-07-26T23:05:49Z` by the incumbent's own cycle. **No lease was disturbed by
stage 1**, because stage 1 touched no path any live lane reads.

Two conditions found in the live system while preparing the cutover, both
recorded because they shape the cutover procedure rather than being caused by it:

1. **The incumbent supervisor is badly degraded.** `poll_interval_seconds` is 30
   but its ticks report `lag=603–894s`, and it burns 3h15m CPU over 7h42m
   wall-clock. It holds the runtime admission `flock` for most of each cycle, so
   governed status mutations queue behind it for minutes — this task's own
   `ai-status.sh start` waited in `locks_lock_inode_wait` and only landed at
   22:48:46Z (`raw/installed-root-verification.txt` §9). This is a pre-existing
   condition of the incumbent runtime, not of the candidate.
2. **The projection and the board diverge between cycles.** At 22:51:02Z an
   independent projection of the journal (2046 events) hashed
   `a95e170e763b…` against a board hash of `c7ec3a4e7e1c…`, while the
   supervisor's last recorded `task_state_shadow` (22:47:35Z, 2039 events) was
   `ok:true, caught_up:false`. In authoritative mode `sync_task_state_shadow`
   repairs the board from the journal on the next successful cycle, so the
   post-cutover check in §6.3 is the one that matters for acceptance.

## 5. Rollback — available now, not yet exercised

Rollback needs no config change either, and does not depend on this task's
worktree:

```bash
# 1. return the installed root to the runtime that was there before stage 1
git -C /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48 \
    checkout --detach d054bd49cb485f091e3fb31b1d91e57d4fe372ab

# 2. or, to restore the runtime that is live today, relaunch from the prior
#    installed root, which stage 1 deliberately did not touch
bash handoff/swap-supervisor.sh /home/lupin/pantheon-ci-deploy/dev-root-bdbd0a99bf68 rollback-bdbd
```

Prior installed root `dev-root-bdbd0a99bf68` is intact at
`bdbd0a99bf68e6a635d9bd936782c659298b7bb7`; prior HEAD of the installed root is
recorded as `d054bd49cb485f091e3fb31b1d91e57d4fe372ab` (tree
`72618947f7f326a6d7664deab9eedbf86d7af861`).

The acceptance item asks for rollback to be **tested**, which means an executed
drill (roll back, verify a healthy boot with leases preserved, roll forward
again). That drill is part of the blocked stage — it needs the same process
permission as the cutover.

## 6. Stage 2 — blocked: supervisor process handoff

### 6.1 What is blocked and why

The handoff requires stopping pid 901543 and launching the replacement from the
installed root. Every attempt from this background worker was refused by the
harness permission layer ("Blocked by classifier"), including the read-mostly
first step:

```
python3 .orchestrator/supervisor_watchdog.py --config <live config> \
  --record-intent-pid 901543 --record-intent-target 6578ef968f7e…
→ denied

bash handoff/swap-supervisor.sh /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48 refresh-d054
→ denied
```

No approval route was available: the `orchestrator_approval_broker` MCP server
exposed no tools to this session. The worker therefore stopped at the last safe
boundary instead of trying to reach the same effect another way.

The intermediate state is deliberately coherent: the live system keeps running
the runtime it was already running, and the only change on disk is that the
config-named watchdog path now holds the accepted runtime instead of an older
one.

### 6.2 The exact remaining command

`handoff/swap-supervisor.sh <new_root> <label>` performs the whole handoff and
fails closed at every step. Its serialization is the point:

1. resolves the live supervisor by `/proc` scan and **requires exactly one** —
   matching the same shape as `supervisor.cmdline_is_supervisor_process` (argv[0]
   must be a python interpreter), because a substring-only match also caught the
   `/bin/bash -c` launcher wrapper that started the incumbent and tripped the
   one-supervisor guard. `--discover-only` reports the resolved runtime and pid
   and exits before any mutation; its dry run is in
   `raw/installed-root-verification.txt` §10;
2. records the durable PID-bound intentional-restart declaration first, so a
   watchdog relaunch is not charged to the crash-loop budget — this call blocks
   on the runtime admission lock, which is what serialises the cutover against
   the in-flight cycle;
3. takes the runtime admission `flock` **before** sending `TERM`, so the
   outgoing supervisor cannot be inside a locked canonical transaction, and
   waits up to 60s for it to exit (never escalating to `KILL`; it aborts instead
   of launching a replacement over a survivor);
4. **releases the lock before launching**, so the new supervisor never inherits
   a held lock file descriptor (that inheritance would deadlock its own
   `runtime_state_lock`);
5. launches `python3.12 -u .orchestrator/supervisor.py --config <live config>
   --verbose` from `<new_root>` under `setsid`, with every `ORCH_*`/`PANTHEON_*`
   variable plus `CLAUDE_CONFIG_DIR`/`GH_CONFIG_DIR` stripped, so no worker
   identity leaks into the supervisor or into the workers it spawns;
6. reports the new pid, its cwd, and the first log lines.

To finish the task:

```bash
cd <this evidence dir>
# optional, no mutation: confirm it resolves one supervisor and the right runtime
bash handoff/swap-supervisor.sh /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48 refresh-d054 --discover-only
# the handoff itself
bash handoff/swap-supervisor.sh /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48 refresh-d054
```

Timing note: worker leases are 600s and lease renewal also needs work-progress
freshness within `work_progress_stale_seconds=360`. `reconcile_runtime_on_boot`
refreshes `last_heartbeat_at` from each runner's heartbeat file before it
judges a lease, and the poll stage re-observes real process/commit progress, so
a prompt handoff preserves every live lease. A supervisor left down long enough
for both the lease **and** observed progress to lapse would let boot
reconciliation terminate live workers — so the swap should be run, not queued.

### 6.3 Verification to record after the handoff

```bash
# 1. process identity and runtime
readlink -f /proc/<new_pid>/cwd        # → …/dev-root-d054bd49cb48
git -C /home/lupin/pantheon-ci-deploy/dev-root-d054bd49cb48 rev-parse HEAD
                                       # → 6578ef968f7e…
sha256sum /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json
                                       # → cd98e7c5c259…  (unchanged)

# 2. lease parity + projection, one snapshot
python3 handoff/runtime-snapshot.py post-refresh > raw/post-refresh-snapshot.json
```

Acceptance is met when the post-refresh snapshot shows:

- the same six (or fewer, as lanes finish naturally) pre-cutover `run_id`s still
  active with **extended** `lease_expires_at` and their original
  `command_root=…/dev-root-bdbd0a99bf68` — a preserved lease keeps the runtime it
  was issued, and only newly dispatched workers carry
  `…/dev-root-d054bd49cb48` + `6578ef968f7e…`;
- no second active worker for any task that already had one (no duplicate
  dispatch), and each `started` queue record's `lease_owner` still naming its
  live worker;
- `supervisor.task_state_shadow.mode=authoritative`, `ok:true`, with
  `projected_state_sha256 == expected_state_sha256` after the first successful
  cycle;
- `activity` containing the supervisor's own `supervisor_replaced` /
  boot-reconciliation rows rather than any hand edit of the task board.

Then run the rollback drill (§5), snapshot again, and roll forward.

## 7. Acceptance mapping

| Acceptance item | State |
|---|---|
| Candidate commit is exact accepted dev with required checks and contains the merged supervisor truth repair | **met** — §1 |
| Installed command root commit and tree exactly match the candidate and retain the existing live config byte for byte | **met** — §3 |
| Refresh is serialized after active workers drain or their leases are safely preserved with no duplicate dispatch | **partly** — stage 1 disturbed no lease (§4); the serialized cutover is implemented and unexecuted (§6.2) |
| Supervisor PID replacement and command runtime handoff are proven without manual task board edits | **blocked** — §6.1 |
| Authoritative event projection shadow caught_up and queue worker lease parity pass after refresh | **blocked** — check defined in §6.3 |
| Rollback to the prior installed root is tested and available | **available, not tested** — §5 |
| Deployment evidence branch commit push PR checks Codex2 review merge and archive complete | in progress — this artifact |

## 8. Candidate freshness while the handoff is pending

`dev` kept advancing after the install: by 2026-07-26T23:2xZ the tip was
`1cf27337e` (PRs #4225 and #4226, both `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001`).
That does not invalidate the installed runtime — `6578ef968f7e` is still an
exact accepted `dev` commit, an ancestor of `origin/dev`, with all three
required contexts green, and it is the commit that carries the dependency this
task exists to activate.

Policy for whoever runs the blocked handoff:

- running §6.2 as recorded installs and activates `6578ef968f7e`, which is
  correct and sufficient for this task's acceptance;
- if the handoff happens much later and a fresher runtime is wanted, re-run the
  §3 install step against the then-current accepted `dev` tip **first**
  (`git -C …/dev-root-d054bd49cb48 checkout --detach <new-tip>`, verifying the
  same HEAD/tree/clean/remote/ancestry and unchanged config sha256), then run
  the same swap. Do not mix: never activate a root whose HEAD is not exactly one
  accepted `dev` commit.

## 9. Residual risks

| Risk | Severity | Containment |
|---|---|---|
| The installed root's directory name no longer matches its HEAD | low | Documented in §2; the name is a human convention, and correcting it would require the config edit this task forbids. The authoritative bindings (`git rev-parse HEAD`, issued `status_command_runtime`) are computed, never parsed from the path. |
| Handoff left unexecuted while the incumbent supervisor stays degraded | medium | Governed status mutations keep queueing behind ~10-minute cycles. Nothing is lost, but latency stays high until §6.2 runs. |
| A watchdog relaunch during the gap | low | Now strictly beneficial: the config-named path holds the accepted runtime, so a relaunch lands on the candidate instead of the older `d054bd49cb48` code. |
| Prior runtime `dev-root-bdbd0a99bf68` carries 22 uncommitted generated task-brief entries | low | Left untouched on purpose; they are supervisor-generated caches, and touching that root would break live worker runtime pins. |
