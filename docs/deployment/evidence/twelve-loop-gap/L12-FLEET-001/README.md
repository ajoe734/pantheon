# L12-FLEET-001 fleet-capacity evidence

Status: ready for independent `Codex2` review.

This packet records the lease-safe reconciliation of the reviewed Codex-family
fleet policy into the split-root supervisor runtime. It does not authorize live
capital, bypass approvals, materialize the four remaining catalog candidates,
or replace independent review.

The machine-readable receipt is in [`evidence.json`](evidence.json), with its
digest in [`evidence.sha256`](evidence.sha256).

## Reviewed source and installed identity

- Task: `L12-FLEET-001`
- Owner: `Codex`
- Reviewer: `Codex2`
- Task branch base: `09159159fbac9b43d3e97011a12a224699677620`
  (`origin/dev` at worker dispatch and final validation)
- Fleet guard source: prerequisite merge commit
  `09159159fbac9b43d3e97011a12a224699677620`, including
  `2ed67c6d6` (`LOOP-GAP-FLEET-001`)
- Evidence anchor:
  `e5fe75bcc9c5e8dcdeed7a7603385301bcfadcac`
- Installed command root:
  `/home/lupin/pantheon-ci-deploy/dev-root`
- Canonical status root: `/home/lupin/pantheon`
- Live config:
  `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`

Before reconciliation, the command root was
`be956c07aca889043ef301389412b6744452f20b`, 22 commits behind
`origin/dev`. The supervisor-issued command identity was already
`09159159fbac9b43d3e97011a12a224699677620`, so governed
`ai-status.sh show/start` correctly failed closed on the mismatch.

At `2026-07-26T08:34:29Z`, the repository-provided
`scripts/sync-dev-root.sh` updated the command root to
`09159159fbac9b43d3e97011a12a224699677620`. It provisioned the live config,
ran the drift gate, and recorded a PID/SHA-bound restart intent. No live JSON
was hand-edited and no process was killed outside that governed flow.

## Deployment-lease safety

Every nonprod run observed before reconciliation was allowed to finish or age
out. No lease was taken over, cancelled, or manually released.

| Run | Result | Lease observation |
| --- | --- | --- |
| [`30192780633`](https://github.com/ajoe734/pantheon/actions/runs/30192780633) | failure | held `f95b33cb-e262-41ff-905b-fdc86567c3a4`; failure quarantine was respected through TTL |
| [`30193454063`](https://github.com/ajoe734/pantheon/actions/runs/30193454063) | failure | failed at the pre-lease Agora gate |
| [`30193620022`](https://github.com/ajoe734/pantheon/actions/runs/30193620022) | failure | held a distinct lease; failure quarantine was respected through `2026-07-26T08:04:08Z` |
| [`30193954071`](https://github.com/ajoe734/pantheon/actions/runs/30193954071) | failure | held `509bb81b-1cd2-4a48-9f08-7f042bbbaab6`; last heartbeat `08:26:02Z`, expiry `08:31:02Z` |
| [`30194698291`](https://github.com/ajoe734/pantheon/actions/runs/30194698291) | failure | failed in payload validation before lease acquisition |

The final sync gate observed:

- zero queued or in-progress nonprod runs;
- GitHub API authoritative time `2026-07-26T08:34:29Z`;
- the last lease expiry at `2026-07-26T08:31:02Z`.

The restart-intent receipt was created at `08:34:31Z`; the sync requested the
old supervisor stop at `08:36:25Z`. The watchdog started new supervisor PID
`1579402` at `08:37:05Z`, replacing PID `829000`.

Run
[`30194930965`](https://github.com/ajoe734/pantheon/actions/runs/30194930965)
was created after the sync had begun. Its workflow log proves that it did not
acquire lease `390601ee-2c69-47cc-b815-1d731fb9fb71` until `08:37:52Z`,
47 seconds after the watchdog restart. It completed successfully and released
that lease at `08:40:35Z`; the remote lease document was absent at final
readback. Therefore the supervisor restart did not overlap an active deploy
lease.

## State preservation across restart

The immediate pre-restart snapshot at `08:33:56Z` and post-restart snapshot at
`08:38:16Z` were identical for the state surfaces in scope:

| Surface | Before | After |
| --- | --- | --- |
| Task count | 25 | 25 |
| Sorted task-ID SHA-256 | `8c29a93200389b3eed95d1b5958ce332475baaeb59f346c05b8ca1233fa214ae` | same |
| Duplicate task IDs | none | none |
| Queue count | 2 | 2 |
| Sorted queue-ID SHA-256 | `724b8d222c784c85e73d101479e3130670cfd9512ba6bb640d9cf0c9df8a35f9` | same |
| Pending approvals | 0 | 0 |
| Pending approval-ID SHA-256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | same |
| Journal rows | 1,203 | 1,203 |
| Journal SHA-256 | `d86ae20c7f5ab546e6cca5d70388fd6d1d5c839db8da58c6f9a4d3c586052be0` | same |
| Journal projection SHA-256 | `1131bc03bfa8e00ffdb79bd7d6d0b171cbb1f58e865e1907ac483dcb525206e1` | same |

`PPL-ALLOC-009` stayed `blocked`, owned by `Codex`, reviewed by `Claude2`,
and waiting for `Human/Ops`. `L12-FLEET-001` stayed `todo` across the restart.
Only after the preservation snapshot did
`AI_NAME=Codex $PANTHEON_COMMAND_ROOT/scripts/ai-status.sh start` move this
task to `in_progress`. The authoritative journal then contained 1,205 events
and still replayed exactly:

```json
{
  "event_count": 1205,
  "expected_state_sha256": "468c48cd368096cecb73d73e93d7fdda950d24d6c0eacfd75b3f343bcdaf43b9",
  "last_event_id": "task-state-85ab283f0b77c93d7ae6ce3c2e5d56247dbf069ea7e3ca2a77fe9a62dd849b28",
  "ok": true,
  "projected_state_sha256": "468c48cd368096cecb73d73e93d7fdda950d24d6c0eacfd75b3f343bcdaf43b9"
}
```

The later removal of the chair-review queue row was normal supervisor
consumption after the exact restart comparison, not restart loss. The
`L12-FLEET-001` dispatch event remained present.

## Policy, capacity, and health

Post-provision `check_config_drift.py --max-behind 0 --json` returned:

```json
{
  "dev_root_behind": 0,
  "drift": [],
  "exit_code": 0,
  "fixed": [],
  "intentional": [],
  "missing": []
}
```

Repository and live `ready_dispatcher` projections both hash to
`83ad5b2125497853b5250dc737f257427ddccf2cf5f7a86e7ba36ec3e7eb9373`.
The effective policy includes:

- `disabled_agents=["Antigravity2","Copilot"]`;
- no `sidecar_only_agents`;
- per-agent caps `Codex=4` and `Codex2=4`;
- account caps `codex1=4` and `codex2=4`;
- `max_dispatches_per_tick=10`;
- `max_concurrent_workers=13`.

Installed-code capacity evaluation returned `{"Codex": 4, "Codex2": 4}`.
Fresh post-restart provider probes reported `auth_ready=true`,
`verified="verified"`, `delivery_mode="codex"`, and
`local_cli_worker_supported=true` for all eight slots:

```text
codex1-1  codex1-2  codex1-3  codex1-4
codex2-1  codex2-2  codex2-3  codex2-4
```

At `08:42:51Z`, `supervisor_runtime_health.py --require-watchdog` returned
`healthy=true`, lifecycle `running`, a fresh `08:42:27Z` heartbeat, no loop
error, and a fresh watchdog probe. The user watchdog timer was both
`active` and `enabled`; provider dispatch pauses were empty.

The health helper's direct process-identity diagnostic reported
`process_alive=false`/`pid_matches=false`, but its lock-held liveness check was
true and the overall check passed. An independent `ps` readback showed PID
`1579402` running the exact installed supervisor/config command. This
diagnostic detail is preserved for reviewer visibility.

## Mutation-free catalog proof

`--validate-only` accepted catalog SHA-256
`a7fbbaa560bd7f2d97750b25cd20af69b64d4f522b293689849b0e1b1763717f`
with 25 tasks.

The installed-root guarded `--dry-run` succeeded against the authoritative
external journal. It reported 21 exact tasks, four create candidates
(`L12-VERIFY-RUNTIME-001`, `L12-VERIFY-OBS-001`, `L12-HOSTED-001`, and
`L12-CLOSE-001`), and blocked external dependency `PPL-ALLOC-009`. It did not
apply those candidates.

The following SHA-256 values were byte-identical before and after the dry-run:

| File | SHA-256 |
| --- | --- |
| `ai-status.json` | `8c74b1ab8640ca68d1f8484962f1492d6ade723a1b5fcf880d69ee64cb124981` |
| `ai-activity-log.jsonl` | `a02805527ab3908a3f0deeffe4518b3d63bdf91416e86250c40bae09acf703b4` |
| `current-work.md` | `d1e800ada045dbe841b71e1a133d2434ac6eda08d45fc7c700ff156cb7471523` |
| `.orchestrator/event-queue.jsonl` | `807d14888705847aa27306dc80f15d0cc1dcd63f85187cfefb123105cf7be6d8` |
| `.orchestrator/approval-queue.json` | `e512ae0b0db03b1cb6fe1c7c29bc0f444ce6774aa270be418fa78f7398559646` |
| external `task-state-events.jsonl` | `2b8e1340300448904d93dd6840b0b73a6e43e2be1cd830177541390dc6fcf100` |

## Validation

```text
PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  scripts/test_provision_live_supervisor_config.py \
  scripts/test_check_config_drift.py \
  scripts/test_dispatch_twelve_loop_gap_2026_07_26.py \
  .orchestrator/test_dispatch_policy.py
82 passed in 7.26s

PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  .orchestrator/test_supervisor.py::RuntimeConfigTests::test_codex_accounts_allow_four_concurrent_slots \
  .orchestrator/test_supervisor.py::RuntimeConfigTests::test_live_provider_account_schema_is_strict_and_complete
2 passed in 0.43s

/home/lupin/pantheon/.venv/bin/python -m py_compile \
  scripts/provision_live_supervisor_config.py \
  scripts/check_config_drift.py \
  scripts/dispatch_twelve_loop_gap_2026_07_26.py \
  .orchestrator/supervisor.py
exit 0
```

All four task acceptance criteria are satisfied. The remaining catalog
candidates are deliberately unmaterialized, and the pre-existing
`PPL-ALLOC-009` Human/Ops blocker is preserved.
